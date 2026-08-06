<!--
  PttFab — guarded sticky floating-action button for mobile PTT.

  Sits above the tuning strip (bottom-right), out of the thumb-path for
  tuning drag. Implements layered anti-accidental-TX guards per
  docs/plans/2026-04-18-mobile-ia.md §5:

    1. Spatial isolation — FAB lives outside tuning strip (primary).
    2. Press-and-hold minimum 50ms — `pointerdown` must persist at least
       50ms before `onDown()` is invoked. Prevents tap-through from
       scroll gestures.
    3. Pointer-move 8px cancel — if the finger moves >8px before the
       50ms window closes, the press is cancelled (user was scrolling,
       not pressing).
    4. Haptic feedback on engage (navigator.vibrate).
    5. TX-permit two-step — when `txPermit === 'denied'` the first press
       only arms the FAB for 2 seconds and surfaces a toast. A second
       press within that window engages.

  State machine is kept in the parent (`pttMode`, safety timer, etc.);
  this component just gates the `onDown/onUp` callbacks behind the
  guards. Parent passes `mode` so visuals reflect held / latched state
  using the existing CSS conventions.

  Part of #840 / epic #818 mobile IA followup.
-->
<script lang="ts">
  import { Mic, MicOff } from 'lucide-svelte';

  interface Props {
    /** Current PTT state from the parent state machine. */
    mode: 'idle' | 'held' | 'latched';
    /** Radio-level TX permit (capability / out-of-band). */
    txPermit?: 'allowed' | 'denied';
    /** Called when a guarded press engages. Parent runs its pttDown(). */
    onDown: () => void;
    /** Called when the press ends. Parent runs its pttUp(). */
    onUp: () => void;
    /** Called when an out-of-band first press is rejected (for toast). */
    onPermitWarning?: () => void;
  }

  let { mode, txPermit = 'allowed', onDown, onUp, onPermitWarning }: Props = $props();

  const HOLD_DELAY_MS = 50;
  const MOVE_CANCEL_PX = 8;
  const PERMIT_ARM_WINDOW_MS = 2000;

  // Gesture state — local to this component.
  let holdTimer: ReturnType<typeof setTimeout> | null = null;
  let engaged = false;               // true once onDown() was invoked
  let startX = 0;
  let startY = 0;
  // MOR-1376: the callbacks the CURRENT press was armed against. The hold timer
  // below is deliberately not cleared on unmount, so a rotation can destroy this
  // FAB mid-delay; reading the `onDown`/`onUp` props at fire time would then
  // resolve to whatever handler the parent has live BY THEN — a recognizer
  // generation that never saw this press. Snapshot them while the press is
  // still this instance's own, and complete the press against those.
  let armedDown: (() => void) | null = null;
  let armedUp: (() => void) | null = null;

  // TX-permit two-step arm state. First press arms; second press within
  // the window engages. A timer resets armedAt back to 0 when the window
  // lapses so the visual "armed" styling doesn't stick after timeout
  // (codex P2 on PR #928).
  let armedAt = $state(0);
  let armedResetTimer: ReturnType<typeof setTimeout> | null = null;

  function vibrate(ms: number) {
    if (typeof navigator !== 'undefined' && typeof navigator.vibrate === 'function') {
      try { navigator.vibrate(ms); } catch { /* silent on unsupported */ }
    }
  }

  function clearHoldTimer() {
    if (holdTimer !== null) {
      clearTimeout(holdTimer);
      holdTimer = null;
    }
  }

  function cancelPress() {
    clearHoldTimer();
    if (engaged) {
      // A press that already engaged — release cleanly, against the handler
      // that press was armed on (MOR-1376).
      armedUp?.();
      engaged = false;
    }
  }

  function handlePointerDown(event: PointerEvent) {
    // Latched mode: delegate immediately so tap unlatches.
    if (mode === 'latched') {
      onDown();
      return;
    }

    // TX-permit two-step: first press arms, second within window engages.
    if (txPermit === 'denied') {
      const now = Date.now();
      if (now - armedAt > PERMIT_ARM_WINDOW_MS) {
        armedAt = now;
        if (armedResetTimer !== null) clearTimeout(armedResetTimer);
        armedResetTimer = setTimeout(() => {
          armedAt = 0;
          armedResetTimer = null;
        }, PERMIT_ARM_WINDOW_MS);
        onPermitWarning?.();
        return;
      }
      // Within the arm window — consume arm state and fall through to engage.
      armedAt = 0;
      if (armedResetTimer !== null) {
        clearTimeout(armedResetTimer);
        armedResetTimer = null;
      }
    }

    // Capture so pointermove/pointerup still fire if the finger slides off.
    (event.currentTarget as HTMLElement).setPointerCapture?.(event.pointerId);
    startX = event.clientX;
    startY = event.clientY;
    engaged = false;
    armedDown = onDown;
    armedUp = onUp;

    holdTimer = setTimeout(() => {
      holdTimer = null;
      engaged = true;
      vibrate(10);
      armedDown?.();
    }, HOLD_DELAY_MS);
  }

  function handlePointerMove(event: PointerEvent) {
    if (holdTimer === null && !engaged) return;
    const dx = event.clientX - startX;
    const dy = event.clientY - startY;
    if (dx * dx + dy * dy > MOVE_CANCEL_PX * MOVE_CANCEL_PX) {
      cancelPress();
    }
  }

  function handlePointerUp() {
    if (holdTimer !== null) {
      // Released before 50ms threshold — no engage at all.
      clearHoldTimer();
      return;
    }
    if (engaged) {
      armedUp?.();
      engaged = false;
    }
  }

  function handleContextMenu(event: Event) {
    // Long-press should engage TX, not show a system context menu.
    event.preventDefault();
  }
</script>

<button
  class="ptt-fab"
  class:ptt-fab-held={mode === 'held'}
  class:ptt-fab-latched={mode === 'latched'}
  class:ptt-fab-armed={txPermit === 'denied' && armedAt !== 0}
  class:ptt-fab-permit-denied={txPermit === 'denied'}
  onpointerdown={handlePointerDown}
  onpointermove={handlePointerMove}
  onpointerup={handlePointerUp}
  onpointercancel={handlePointerUp}
  onlostpointercapture={() => cancelPress()}
  oncontextmenu={handleContextMenu}
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
    position: fixed;
    right: 12px;
    bottom: calc(52px + env(safe-area-inset-bottom, 0px) + 12px);
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
    z-index: 100;
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

  /* Out-of-band: dim border until armed — signals "not allowed". */
  .ptt-fab-permit-denied {
    border-color: rgba(239, 68, 68, 0.35);
    color: rgba(239, 68, 68, 0.5);
  }

  .ptt-fab-armed {
    /* Armed (first tap after denial): brighter border within 2s window. */
    border-color: rgba(250, 204, 21, 0.85);
    color: var(--v2-accent-yellow, #facc15);
  }
</style>
