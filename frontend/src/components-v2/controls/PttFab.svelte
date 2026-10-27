<!--
  PttFab — guarded PTT button for mobile PTT.

  Lives in the portrait tuning strip (MobileRadioLayout's fixed bottom
  bar, OUTSIDE the one phone scroller): its own reserved place where no
  scrolling control can ever sit under it at any scroll position
  (MOR-2874; the floating-over-the-scroller placement it replaced drew
  the button over the MUTE key of the ESSENTIALS grid in the approved
  portrait baseline). The host owns placement — a gap-separated flex
  sibling of the tuning keys; this component owns the look and the
  layered anti-accidental-TX guards per
  docs/plans/2026-04-18-mobile-ia.md §5:

    1. Spatial isolation — the strip is fixed chrome outside the
       scroller; nothing scrolls under the button (MOR-2874).
    2. Press-and-hold minimum 50ms — `pointerdown` must persist at least
       50ms before the press engages. Prevents tap-through from
       scroll gestures.
    3. Pointer-move 8px cancel — if the finger moves >8px before the
       50ms window closes, the press is cancelled (user was scrolling,
       not pressing).
    4. TX permit remains advisory presentation; server authority admits PTT.

  The hold is the parent's `hold`; this component attaches it with
  guards 2 and 3. Parent passes `mode` so visuals reflect held /
  latched server state using the existing CSS conventions.

  Part of #840 / epic #818 mobile IA followup.
-->
<script lang="ts">
  import { Mic, MicOff } from 'lucide-svelte';
  import type { PttHold } from '../wiring/managed-tx-gesture';

  interface Props {
    /** Current PTT state from the parent state machine. */
    mode: 'idle' | 'held' | 'latched';
    /** Advisory radio-level TX permit used only for presentation. */
    txPermit?: 'allowed' | 'denied';
    /** The parent's hold; it outlives this button (MOR-3817). */
    hold: PttHold;
  }

  let { mode, txPermit = 'allowed', hold }: Props = $props();

  // Latched mode skips the guard so a tap unlatches at once.
  const guard = () => (mode === 'latched' ? null : { delayMs: 50, moveCancelPx: 8 });
  const holdButton = (node: HTMLElement) => hold.attach(node, { guard });
</script>

<button
  class="ptt-fab"
  class:ptt-fab-held={mode === 'held'}
  class:ptt-fab-latched={mode === 'latched'}
  class:ptt-fab-permit-denied={txPermit === 'denied'}
  use:holdButton
  aria-label={mode === 'latched' ? 'Release TX lock' : 'Push to talk'}
>
  {#if mode === 'latched'}
    <MicOff size={28} />
    <span class="ptt-fab-label">TX LOCK</span>
  {:else if mode === 'held'}
    <span class="ptt-fab-tx">TX</span>
  {:else}
    <Mic size={28} />
    <span class="ptt-fab-label">PTT</span>
  {/if}
</button>

<style>
  .ptt-fab {
    /* In-flow inside the portrait tuning strip (MOR-2874): the host
       places it; this block owns only the button's own look. */
    width: 72px;
    height: 72px;
    border-radius: 50%;
    border: 2.5px solid rgba(239, 68, 68, 0.9);
    background: rgba(239, 68, 68, 0.08);
    color: var(--v2-accent-red, #ef4444);
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 2px;
    font-family: 'Roboto Mono', monospace;
    font-weight: 700;
    letter-spacing: 0.1em;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.35);
    user-select: none;
    touch-action: none;  /* block scroll / pinch on the FAB itself */
    transition: background 0.08s, border-color 0.08s, box-shadow 0.12s;
  }

  .ptt-fab-label,
  .ptt-fab-tx {
    font-size: 11px;
  }

  .ptt-fab-tx {
    font-size: 22px;
    letter-spacing: 0.05em;
  }

  .ptt-fab-held {
    background: var(--v2-accent-red, #ef4444);
    color: #fff;
    box-shadow:
      0 0 16px rgba(239, 68, 68, 0.65),
      0 4px 12px rgba(0, 0, 0, 0.4);
  }

  .ptt-fab-latched {
    background: #991b1b;
    border-color: #7f1d1d;
    color: #fff;
    animation: ptt-fab-latch-pulse 1.2s ease-in-out infinite;
  }

  @keyframes ptt-fab-latch-pulse {
    0%, 100% { box-shadow: 0 0 0 rgba(239, 68, 68, 0.4), 0 4px 12px rgba(0, 0, 0, 0.4); }
    50%      { box-shadow: 0 0 22px rgba(239, 68, 68, 0.9), 0 4px 12px rgba(0, 0, 0, 0.4); }
  }

  /* Advisory permit styling never gates the server-admitted intent. */
  .ptt-fab-permit-denied {
    border-color: rgba(239, 68, 68, 0.35);
    color: rgba(239, 68, 68, 0.5);
  }
</style>
