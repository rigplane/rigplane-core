<script lang="ts">
  import { onDestroy } from 'svelte';
  import {
    normalizeKeyboardConfig,
    resolveAction,
    resolveSequenceContinuation,
    resolveSequenceStart,
    shouldIgnoreEvent,
    isDigitKey,
    isFrequencyDisplayFocused,
    formatShortcut,
    type KeyboardActionConfig,
    type KeyboardBindingConfig,
    type KeyboardConfig,
  } from './keyboard-map';

  interface Props {
    config?: KeyboardConfig | null;
    onAction?: (action: KeyboardActionConfig) => void;
    enabled?: boolean;
  }

  let {
    config = null,
    onAction = () => {},
    enabled = true,
  }: Props = $props();

  let keyboardConfig = $derived(normalizeKeyboardConfig(config));
  let pendingSequence = $state<KeyboardBindingConfig | null>(null);
  let leaderLabel = $state<string | null>(null);
  let helpOpen = $state(false);
  let leaderTimer: ReturnType<typeof setTimeout> | null = null;

  function groupBindings(bindings: KeyboardBindingConfig[]): Array<[string, KeyboardBindingConfig[]]> {
    const groups = new Map<string, KeyboardBindingConfig[]>();
    for (const binding of bindings) {
      const section = binding.section || 'General';
      const bucket = groups.get(section) ?? [];
      bucket.push(binding);
      groups.set(section, bucket);
    }
    return [...groups.entries()];
  }

  let groupedBindings = $derived(groupBindings(keyboardConfig.bindings));

  function clearLeaderState(): void {
    pendingSequence = null;
    leaderLabel = null;
    if (leaderTimer) {
      clearTimeout(leaderTimer);
      leaderTimer = null;
    }
  }

  function dispatch(action: KeyboardActionConfig): void {
    if (action.action === 'toggle_help') {
      helpOpen = !helpOpen;
      return;
    }
    onAction(action);
  }

  /**
   * MOR-1444: seeds BandSurface's frequency-entry input with a digit typed
   * while the VFO/frequency display has focus, and moves focus into it.
   * `[data-freq-entry]` is a production hook on that input (BandSurface.svelte)
   * distinct from its test id. Every following keystroke targets the
   * now-focused INPUT, which `shouldIgnoreEvent` already excludes from
   * shortcut resolution above — so only the FIRST digit of a typed-entry
   * gesture needs this routing; native typing takes it from there. Resets
   * (rather than appends to) any stale leftover value, since this is the
   * start of a fresh gesture. Returns false — letting the caller fall
   * through to normal band-hotkey resolution — when the entry surface isn't
   * mounted or is disabled (no band group on this skin, or BandSurface's own
   * MOR-1322/rule-5 fail-closed gates: active receiver or tuning bounds not
   * yet known).
   */
  function routeDigitToFrequencyEntry(digit: string): boolean {
    const input = document.querySelector<HTMLInputElement>('[data-freq-entry]');
    if (!input || input.disabled) return false;
    input.focus();
    input.value = digit;
    input.dispatchEvent(new Event('input', { bubbles: true }));
    return true;
  }

  function handleKeydown(event: KeyboardEvent): void {
    if (!enabled) return;
    if (shouldIgnoreEvent(document.activeElement)) return;

    // MOR-1444: with the VFO/frequency display focused, a digit keystroke
    // must feed the frequency-entry box instead of resolving as a band
    // hotkey — every rig profile's keyboard config binds "1".."9" to
    // band_select, and without this guard `resolveAction` below hops bands
    // on every typed digit. Band hotkeys are untouched everywhere else.
    // MOR-1444 B3 (round-2 review): a Ctrl/Cmd/Alt-modified digit is a
    // browser or OS shortcut (Cmd+1 switches tabs in most browsers), never a
    // frequency-entry keystroke — it must reach neither the entry input nor
    // preventDefault().
    if (
      isDigitKey(event.key) && !event.ctrlKey && !event.metaKey && !event.altKey
      && isFrequencyDisplayFocused(document.activeElement)
    ) {
      if (routeDigitToFrequencyEntry(event.key)) {
        // MOR-1444 B2 (round-2 review): mirrors the MOR-1449 Tab guard
        // immediately below — an early return must still disarm a pending
        // leader sequence, or the pill stays armed and a later keystroke
        // (once focus leaves the now-focused, ignored-tag entry input) can
        // complete an unintended leader sequence.
        if (pendingSequence) clearLeaderState();
        event.preventDefault();
        return;
      }
    }

    // MOR-1449: Tab is reserved for the browser's native focus traversal and
    // must never be assignable to a shortcut — a rig profile's keyboard
    // config is not trusted to keep it free (rigs/_keyboard-default.toml
    // used to bind it to "swap-vfo"; every rig profile inherits that shared
    // default via rig_loader.py's merge). Bail out before any sequence/
    // binding resolution can call preventDefault() on it. A pending leader
    // sequence must still be disarmed — Tab already didn't preventDefault
    // there pre-fix (resolveSequenceContinuation only matches the recorded
    // second key), but skipping clearLeaderState() would leave the leader
    // pill armed and swallow the NEXT keystroke for up to leaderTimeoutMs.
    if (event.key === 'Tab') {
      if (pendingSequence) clearLeaderState();
      return;
    }

    if (event.key === 'Alt' && keyboardConfig.altHints) {
      document.body.dataset.shortcutHints = 'true';
      return;
    }

    if (pendingSequence) {
      const continuation = resolveSequenceContinuation(pendingSequence, event);
      clearLeaderState();
      if (continuation) {
        event.preventDefault();
        dispatch(continuation);
      }
      return;
    }

    const sequenceStart = resolveSequenceStart(event, keyboardConfig);
    if (sequenceStart) {
      event.preventDefault();
      pendingSequence = sequenceStart;
      leaderLabel = formatShortcut(sequenceStart);
      leaderTimer = setTimeout(() => {
        clearLeaderState();
      }, keyboardConfig.leaderTimeoutMs);
      return;
    }

    const action = resolveAction(event, keyboardConfig);
    if (!action) return;
    event.preventDefault();
    dispatch(action);
  }

  function handleKeyup(event: KeyboardEvent): void {
    if (event.key === 'Alt') {
      delete document.body.dataset.shortcutHints;
    }
  }

  onDestroy(() => {
    clearLeaderState();
    delete document.body.dataset.shortcutHints;
  });
</script>

<svelte:window onkeydown={handleKeydown} onkeyup={handleKeyup} />

{#if leaderLabel}
  <div class="keyboard-leader-pill" aria-live="polite">
    {leaderLabel}
  </div>
{/if}

{#if helpOpen}
  <div class="keyboard-help-overlay" role="dialog" aria-modal="true" aria-label={keyboardConfig.helpTitle}>
    <button class="keyboard-help-backdrop" type="button" aria-label="Close keyboard help" onclick={() => (helpOpen = false)}></button>
    <div class="keyboard-help-card">
      <div class="keyboard-help-header">
        <div>
          <div class="keyboard-help-title">{keyboardConfig.helpTitle}</div>
          <div class="keyboard-help-subtitle">Hold Alt to reveal inline shortcut hints on controls.</div>
        </div>
        <button class="keyboard-help-close" type="button" onclick={() => (helpOpen = false)}>Close</button>
      </div>
      <div class="keyboard-help-grid">
        {#each groupedBindings as [section, bindings]}
          <section class="keyboard-help-section">
            <h3>{section}</h3>
            <div class="keyboard-help-list">
              {#each bindings as binding}
                <div class="keyboard-help-row">
                  <div class="keyboard-help-action">
                    <span class="keyboard-help-label">{binding.label ?? binding.id}</span>
                    {#if binding.description}
                      <span class="keyboard-help-description">{binding.description}</span>
                    {/if}
                  </div>
                  <kbd>{formatShortcut(binding)}</kbd>
                </div>
              {/each}
            </div>
          </section>
        {/each}
      </div>
    </div>
  </div>
{/if}

<style>
  .keyboard-leader-pill {
    position: fixed;
    top: 14px;
    right: 14px;
    z-index: 1100;
    padding: 8px 12px;
    border-radius: 999px;
    border: 1px solid var(--v2-border-cyan);
    background: var(--v2-overlay-dark);
    color: var(--v2-text-primary);
    font: 600 12px/1.2 'Roboto Mono', monospace;
    box-shadow: var(--v2-shadow-md);
    backdrop-filter: blur(8px);
  }

  .keyboard-help-overlay {
    position: fixed;
    inset: 0;
    z-index: 1200;
    display: grid;
    place-items: center;
  }

  .keyboard-help-backdrop {
    position: absolute;
    inset: 0;
    border: 0;
    background: var(--v2-modal-backdrop);
    backdrop-filter: blur(10px);
  }

  .keyboard-help-card {
    position: relative;
    width: min(1100px, calc(100vw - 32px));
    max-height: calc(100vh - 40px);
    overflow: auto;
    border: 1px solid var(--v2-border-soft);
    border-radius: 18px;
    background:
      radial-gradient(circle at top left, var(--v2-modal-glow), transparent 32%),
      linear-gradient(180deg, var(--v2-modal-bg-top), var(--v2-modal-bg-bottom));
    color: var(--v2-text-primary);
    box-shadow: var(--v2-shadow-lg);
  }

  .keyboard-help-header {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 16px;
    padding: 24px 24px 18px;
    border-bottom: 1px solid var(--v2-border-softer);
  }

  .keyboard-help-title {
    font: 700 24px/1.1 'IBM Plex Sans', sans-serif;
    letter-spacing: 0.01em;
  }

  .keyboard-help-subtitle {
    margin-top: 8px;
    color: var(--v2-text-secondary);
    font: 500 13px/1.5 'Roboto Mono', monospace;
  }

  .keyboard-help-close {
    border: 1px solid var(--v2-border-soft);
    border-radius: 999px;
    background: var(--v2-overlay-medium);
    color: var(--v2-text-primary);
    padding: 8px 14px;
    font: 600 12px/1 'Roboto Mono', monospace;
    cursor: pointer;
  }

  .keyboard-help-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
    gap: 16px;
    padding: 20px 24px 24px;
  }

  .keyboard-help-section {
    border: 1px solid var(--v2-border-softer);
    border-radius: 14px;
    background: var(--v2-overlay-light);
    overflow: hidden;
  }

  .keyboard-help-section h3 {
    margin: 0;
    padding: 12px 14px;
    border-bottom: 1px solid var(--v2-border-subtle-soft);
    color: var(--v2-accent-cyan-bright);
    font: 700 12px/1 'Roboto Mono', monospace;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }

  .keyboard-help-list {
    display: flex;
    flex-direction: column;
  }

  .keyboard-help-row {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 14px;
    padding: 12px 14px;
    border-top: 1px solid var(--v2-modal-section-border);
  }

  .keyboard-help-row:first-child {
    border-top: 0;
  }

  .keyboard-help-action {
    display: flex;
    flex-direction: column;
    gap: 4px;
    min-width: 0;
  }

  .keyboard-help-label {
    font: 600 13px/1.35 'IBM Plex Sans', sans-serif;
    color: var(--v2-text-bright);
  }

  .keyboard-help-description {
    color: var(--v2-text-secondary);
    font: 500 11px/1.45 'Roboto Mono', monospace;
  }

  kbd {
    white-space: nowrap;
    border: 1px solid var(--v2-border-cyan);
    border-bottom-color: var(--v2-modal-key-active-border);
    border-radius: 8px;
    background: var(--v2-overlay-dark);
    color: var(--v2-text-light);
    padding: 6px 9px;
    font: 600 11px/1 'Roboto Mono', monospace;
    box-shadow: inset 0 -1px 0 var(--v2-modal-key-active-glow);
  }

  @media (max-width: 640px) {
    .keyboard-help-header {
      flex-direction: column;
    }

    .keyboard-help-grid {
      grid-template-columns: 1fr;
    }
  }

  :global(body[data-shortcut-hints='true'] [data-shortcut-hint]) {
    position: relative;
  }

  :global(body[data-shortcut-hints='true'] [data-shortcut-hint]::after) {
    content: attr(data-shortcut-hint);
    position: absolute;
    left: 50%;
    bottom: calc(100% + 6px);
    transform: translateX(-50%);
    z-index: 1300;
    padding: 4px 7px;
    border: 1px solid var(--v2-modal-code-border);
    border-radius: 7px;
    background: var(--v2-modal-code-bg);
    color: var(--v2-text-light);
    font: 600 10px/1 'Roboto Mono', monospace;
    white-space: nowrap;
    pointer-events: none;
    box-shadow: 0 8px 20px var(--v2-modal-shadow);
  }
</style>
