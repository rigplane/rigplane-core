<!--
  Semantic memory-channel surface (MOR-2425 phase A).

  Presentation only, same discipline as every other file in this directory:
  holds no radio state and consults no controller. Memory channels are not
  in the MOR-1262 RadioViewModel vocabulary at all (the radio itself cannot
  report their contents — see `MemoryPanel.svelte`'s own docstring), so
  facts arrive as `toMemoryPanelProps`'s own shape (`activeFreqHz`/
  `activeMode`/`vfoIdentityKnown`) via the `facts` prop, imported as a TYPE
  ONLY from `lib/runtime/props/panel-props.ts` — reusing the one shape
  rather than re-deriving it, with zero runtime dependency on the runtime
  layer (the import is erased at build).

  `onRecall`/`onStore`/`onClear` are the same three-callback shape
  `MemoryPanel.svelte` already wires to `makeMemoryHandlers()`
  (`panel-commands.ts`) — a caller does the same wiring for this surface.
  `onRename` has no radio-side counterpart: nothing in `panel-commands.ts`
  owns a rename intent, because a channel name is local-only bookkeeping
  the radio never sees. It is an optional notification hook a caller may
  ignore; the rename itself always persists through the shared
  `memory-channels.ts` module below, whether or not a caller passes it.

  WRONG-VFO GUARD, same doctrine as `RitXitScanSurface.svelte`'s header:
  `activeFreqHz`/`activeMode` can be finite/real even while
  `vfoIdentityKnown` is false (a dual-receiver bootstrap epoch can report a
  real MAIN-receiver reading before the radio has confirmed MAIN is really
  the active one) — attributing that reading to "the VFO about to be
  stored" would be dishonest. The active-VFO readout below is gated on
  `facts.vfoIdentityKnown` FIRST, never on `Number.isFinite` alone, and the
  store affordance is refused (no callback fires) under the same gate.

  NOT YET WIRED (MOR-2425 phase B). No zone mounts this component, and no
  `memory` entry exists in `SEMANTIC_SURFACE_NAMES` yet — see the phase A
  handoff report for why that specific step did not land in this change.
-->
<script module lang="ts">
  import { MAX_MEMORY_CHANNELS, loadMemoryChannels, persistMemoryChannels } from './memory-channels';
  export { MAX_MEMORY_CHANNELS };
  export const UNKNOWN_TEXT = '—';
</script>

<script lang="ts">
  import { formatFrequencyString } from '../components-v2/display/frequency-format';
  import type { MemoryPanelProps } from '../lib/runtime/props/panel-props';

  interface Props {
    facts: MemoryPanelProps;
    onRecall?: (channel: number) => boolean;
    onStore?: (channel: number, frequencyHz: number, mode: string) => boolean;
    onClear?: (channel: number) => boolean;
    onRename?: (channel: number, name: string) => void;
  }
  let { facts, onRecall, onStore, onClear, onRename }: Props = $props();

  let channels = $state(loadMemoryChannels());
  let selectedChannel = $state(1);
  let showEmpty = $state(false);
  let editingName = $state<number | null>(null);
  let editNameValue = $state('');
  let confirmClear = $state<number | null>(null);
  let storeTarget = $state<number | null>(null);

  function findNextEmpty(): number {
    for (let i = 1; i <= MAX_MEMORY_CHANNELS; i++) {
      if (!channels.has(i)) return i;
    }
    return 1;
  }

  function recallChannel(ch: number): void {
    if (!onRecall?.(ch)) return;
    selectedChannel = ch;
  }

  function storeVfoToChannel(ch: number): void {
    // Refusal, not merely a disabled control: an unattributed VFO reading
    // must never reach `onStore`, even if a caller bypasses the disabled
    // button (see the WRONG-VFO GUARD note above).
    if (!facts.vfoIdentityKnown) return;
    if (!onStore?.(ch, facts.activeFreqHz, facts.activeMode)) return;
    const updated = new Map(channels);
    updated.set(ch, { freq: facts.activeFreqHz, mode: facts.activeMode, name: channels.get(ch)?.name ?? '' });
    channels = updated;
    persistMemoryChannels(channels);
    storeTarget = null;
  }

  function clearChannel(ch: number): void {
    if (!onClear?.(ch)) return;
    const updated = new Map(channels);
    updated.delete(ch);
    channels = updated;
    persistMemoryChannels(channels);
    confirmClear = null;
  }

  function startEditName(ch: number): void {
    editingName = ch;
    editNameValue = channels.get(ch)?.name ?? '';
  }

  function saveEditName(ch: number): void {
    const entry = channels.get(ch);
    const name = editNameValue.slice(0, 10);
    if (entry) {
      const updated = new Map(channels);
      updated.set(ch, { ...entry, name });
      channels = updated;
      persistMemoryChannels(channels);
      onRename?.(ch, name);
    }
    editingName = null;
  }

  let channelList = $derived(
    Array.from({ length: MAX_MEMORY_CHANNELS }, (_, i) => i + 1)
      .filter((ch) => showEmpty || channels.has(ch)),
  );
  let populatedCount = $derived(channels.size);
  /** WRONG-VFO GUARD (file header) — gated on `vfoIdentityKnown` first, not
   *  on whether the raw fields happen to be finite/non-sentinel. */
  let activeFreqText = $derived(facts.vfoIdentityKnown ? formatFrequencyString(facts.activeFreqHz) : UNKNOWN_TEXT);
  let activeModeText = $derived(facts.vfoIdentityKnown ? facts.activeMode : UNKNOWN_TEXT);
</script>

<section class="memory-surface" data-testid="memory-surface" aria-label="Memory channels">
  <div class="memory-toolbar">
    <label class="show-empty">
      <input type="checkbox" bind:checked={showEmpty} />
      <span>All</span>
    </label>
    <span class="channel-count" data-testid="memory-count">{populatedCount}/{MAX_MEMORY_CHANNELS}</span>
    <span class="active-vfo" data-testid="memory-active-vfo" data-observed={facts.vfoIdentityKnown}>
      {activeFreqText} {activeModeText}
    </span>
    <button
      type="button"
      class="store-btn"
      data-testid="memory-store-toggle"
      disabled={!facts.vfoIdentityKnown}
      data-disabled-reason={!facts.vfoIdentityKnown ? 'vfo-identity-unknown' : undefined}
      onclick={() => { storeTarget = storeTarget === null ? findNextEmpty() : null; }}
    >
      VFO {'->'} M
    </button>
  </div>

  {#if storeTarget !== null}
    <div class="store-bar">
      <label class="store-label">
        Store to CH
        <input type="number" class="store-input" min="1" max={MAX_MEMORY_CHANNELS} bind:value={storeTarget} />
      </label>
      <button
        type="button" class="action-btn store-confirm" data-testid="memory-store-confirm"
        disabled={!facts.vfoIdentityKnown}
        data-disabled-reason={!facts.vfoIdentityKnown ? 'vfo-identity-unknown' : undefined}
        onclick={() => storeVfoToChannel(storeTarget!)}
      >
        Store
      </button>
      <button type="button" class="action-btn cancel-btn" onclick={() => (storeTarget = null)}>Cancel</button>
    </div>
  {/if}

  <div class="channel-list" role="list">
    {#each channelList as ch (ch)}
      {@const entry = channels.get(ch)}
      <div
        class="channel-row" class:selected={ch === selectedChannel} class:empty={!entry}
        role="listitem" data-channel={ch} data-testid={`memory-channel-${ch}`}
      >
        <span class="ch-number">{String(ch).padStart(2, '0')}</span>

        {#if entry}
          <span class="ch-freq" data-testid={`memory-channel-${ch}-freq`}>{formatFrequencyString(entry.freq)}</span>
          <span class="ch-mode">{entry.mode || UNKNOWN_TEXT}</span>

          {#if editingName === ch}
            <input
              type="text" class="ch-name-input" maxlength="10" bind:value={editNameValue}
              data-testid={`memory-channel-${ch}-name-input`}
              onkeydown={(e) => { if (e.key === 'Enter') saveEditName(ch); if (e.key === 'Escape') editingName = null; }}
              onblur={() => saveEditName(ch)}
            />
          {:else}
            <button
              type="button" class="ch-name" title="Click to edit name"
              data-testid={`memory-channel-${ch}-name`}
              onclick={() => startEditName(ch)}
            >
              {entry.name || UNKNOWN_TEXT}
            </button>
          {/if}

          <div class="ch-actions">
            <button
              type="button" class="action-btn recall-btn" title="Recall to VFO"
              data-testid={`memory-channel-${ch}-recall`}
              disabled={!facts.vfoIdentityKnown}
              data-disabled-reason={!facts.vfoIdentityKnown ? 'vfo-identity-unknown' : undefined}
              onclick={() => recallChannel(ch)}
            >
              {'>>'}VFO
            </button>

            {#if confirmClear === ch}
              <button type="button" class="action-btn clear-confirm" onclick={() => clearChannel(ch)}>Yes</button>
              <button type="button" class="action-btn cancel-btn" onclick={() => (confirmClear = null)}>No</button>
            {:else}
              <button
                type="button" class="action-btn clear-btn" title="Clear channel"
                data-testid={`memory-channel-${ch}-clear`}
                onclick={() => (confirmClear = ch)}
              >
                CLR
              </button>
            {/if}
          </div>
        {:else}
          <span class="ch-empty-label">-- empty --</span>
          <div class="ch-actions">
            <button
              type="button" class="action-btn store-btn-inline" title="Store VFO to this channel"
              data-testid={`memory-channel-${ch}-store`}
              disabled={!facts.vfoIdentityKnown}
              data-disabled-reason={!facts.vfoIdentityKnown ? 'vfo-identity-unknown' : undefined}
              onclick={() => storeVfoToChannel(ch)}
            >
              {'<<'}VFO
            </button>
          </div>
        {/if}
      </div>
    {/each}
  </div>
</section>

<style>
  /* Structure only — a design language owns colour (MOR-977, forced-colors). */
  .memory-surface { display: flex; flex-direction: column; gap: 0.25rem; }
  .memory-toolbar { display: flex; align-items: center; gap: 0.5rem; }
  .channel-count { margin-left: auto; }
  .channel-list { display: flex; flex-direction: column; }
  .channel-row { display: flex; align-items: center; gap: 0.375rem; }
  .ch-actions { display: flex; gap: 0.1875rem; margin-left: auto; }
  [data-observed='false'] { font-style: italic; }
  button:disabled, input:disabled { cursor: not-allowed; }
</style>
