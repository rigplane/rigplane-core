<script lang="ts">
  /**
   * MemoryPanel — Memory channel manager for IC-7610.
   *
   * The IC-7610 does not support reading memory channel contents via CI-V,
   * so this panel tracks channel data locally (localStorage) and provides
   * controls for recall, store, clear, and channel selection.
   */
  import { runtime } from '$lib/runtime';
  import { deriveMemoryPanelProps } from '$lib/runtime/adapters/panel-adapters';
  import { formatFrequencyString } from '../display/frequency-format';

  let p = $derived(deriveMemoryPanelProps());

  const STORAGE_KEY = 'rigplane:memory-channels';
  const MAX_CHANNELS = 99;

  interface MemoryEntry {
    freq: number;
    mode: string;
    name: string;
  }

  // Local state
  let channels = $state<Map<number, MemoryEntry>>(new Map());
  let selectedChannel = $state(1);
  let showEmpty = $state(false);
  let editingName = $state<number | null>(null);
  let editNameValue = $state('');
  let confirmClear = $state<number | null>(null);
  let storeTarget = $state<number | null>(null);

  // Load from localStorage on init
  if (typeof window !== 'undefined') {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      if (stored) {
        const parsed = JSON.parse(stored) as Record<string, MemoryEntry>;
        const map = new Map<number, MemoryEntry>();
        for (const [k, v] of Object.entries(parsed)) {
          map.set(Number(k), v);
        }
        channels = map;
      }
    } catch { /* ignore */ }
  }

  function persist() {
    try {
      const obj: Record<string, MemoryEntry> = {};
      for (const [k, v] of channels) {
        obj[String(k)] = v;
      }
      localStorage.setItem(STORAGE_KEY, JSON.stringify(obj));
    } catch { /* ignore */ }
  }

  function recallChannel(ch: number) {
    runtime.send('set_memory_mode', { channel: ch });
    runtime.send('memory_to_vfo', { channel: ch });
    selectedChannel = ch;
  }

  function storeVfoToChannel(ch: number) {
    const freq = p.activeFreqHz;
    const mode = p.activeMode;

    runtime.send('set_memory_mode', { channel: ch });
    runtime.send('memory_write', {});

    // Track locally
    const updated = new Map(channels);
    updated.set(ch, { freq, mode, name: channels.get(ch)?.name ?? '' });
    channels = updated;
    persist();
    storeTarget = null;
  }

  function clearChannel(ch: number) {
    runtime.send('memory_clear', { channel: ch });

    // Remove from local tracking
    const updated = new Map(channels);
    updated.delete(ch);
    channels = updated;
    persist();
    confirmClear = null;
  }

  function startEditName(ch: number) {
    editingName = ch;
    editNameValue = channels.get(ch)?.name ?? '';
  }

  function saveEditName(ch: number) {
    const entry = channels.get(ch);
    if (entry) {
      const updated = new Map(channels);
      updated.set(ch, { ...entry, name: editNameValue.slice(0, 10) });
      channels = updated;
      persist();
    }
    editingName = null;
  }

  function findNextEmpty(): number {
    for (let i = 1; i <= MAX_CHANNELS; i++) {
      if (!channels.has(i)) return i;
    }
    return 1;
  }

  let channelList = $derived(
    Array.from({ length: MAX_CHANNELS }, (_, i) => i + 1)
      .filter((ch) => showEmpty || channels.has(ch))
  );

  let populatedCount = $derived(channels.size);
</script>

<div class="memory-panel">
  <div class="memory-toolbar">
    <label class="show-empty">
      <input type="checkbox" bind:checked={showEmpty} />
      <span>All</span>
    </label>
    <span class="channel-count">{populatedCount}/{MAX_CHANNELS}</span>
    <button
      type="button"
      class="store-btn"
      onclick={() => { storeTarget = storeTarget === null ? findNextEmpty() : null; }}
    >
      VFO {'->'} M
    </button>
  </div>

  {#if storeTarget !== null}
    <div class="store-bar">
      <label class="store-label">
        Store to CH
        <input
          type="number"
          class="store-input"
          min="1"
          max={MAX_CHANNELS}
          bind:value={storeTarget}
        />
      </label>
      <button type="button" class="action-btn store-confirm" onclick={() => storeVfoToChannel(storeTarget!)}>
        Store
      </button>
      <button type="button" class="action-btn cancel-btn" onclick={() => (storeTarget = null)}>
        Cancel
      </button>
    </div>
  {/if}

  <div class="channel-list" role="list">
    {#if channelList.length === 0}
      <div class="empty-state">
        No stored channels. Use "VFO {'->'}  M" to store the current frequency.
      </div>
    {/if}

    {#each channelList as ch (ch)}
      {@const entry = channels.get(ch)}
      {@const isSelected = ch === selectedChannel}
      <div
        class="channel-row"
        class:selected={isSelected}
        class:empty={!entry}
        role="listitem"
        data-channel={ch}
      >
        <span class="ch-number">{String(ch).padStart(2, '0')}</span>

        {#if entry}
          <span class="ch-freq">{formatFrequencyString(entry.freq)}</span>
          <span class="ch-mode">{entry.mode || '---'}</span>

          {#if editingName === ch}
            <input
              type="text"
              class="ch-name-input"
              maxlength="10"
              bind:value={editNameValue}
              onkeydown={(e) => { if (e.key === 'Enter') saveEditName(ch); if (e.key === 'Escape') editingName = null; }}
              onblur={() => saveEditName(ch)}
            />
          {:else}
            <button
              type="button"
              class="ch-name"
              title="Click to edit name"
              onclick={() => startEditName(ch)}
            >
              {entry.name || '---'}
            </button>
          {/if}

          <div class="ch-actions">
            <button
              type="button"
              class="action-btn recall-btn"
              title="Recall to VFO"
              onclick={() => recallChannel(ch)}
            >
              {'>>'}VFO
            </button>

            {#if confirmClear === ch}
              <button
                type="button"
                class="action-btn clear-confirm"
                onclick={() => clearChannel(ch)}
              >
                Yes
              </button>
              <button
                type="button"
                class="action-btn cancel-btn"
                onclick={() => (confirmClear = null)}
              >
                No
              </button>
            {:else}
              <button
                type="button"
                class="action-btn clear-btn"
                title="Clear channel"
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
              type="button"
              class="action-btn store-btn-inline"
              title="Store VFO to this channel"
              onclick={() => storeVfoToChannel(ch)}
            >
              {'<<'}VFO
            </button>
          </div>
        {/if}
      </div>
    {/each}
  </div>
</div>

<style>
  .memory-panel {
    display: flex;
    flex-direction: column;
    gap: 4px;
    padding: 6px 8px 8px;
    font-family: 'Roboto Mono', monospace;
    font-size: 11px;
    max-height: 400px;
  }

  .memory-toolbar {
    display: flex;
    align-items: center;
    gap: 8px;
    padding-bottom: 4px;
    border-bottom: 1px solid var(--v2-border, #333);
  }

  .show-empty {
    display: flex;
    align-items: center;
    gap: 4px;
    color: var(--v2-text-dim, #888);
    font-size: 10px;
    cursor: pointer;
    user-select: none;
  }

  .show-empty input {
    width: 12px;
    height: 12px;
    margin: 0;
    accent-color: var(--v2-accent-cyan, #00d4ff);
  }

  .channel-count {
    color: var(--v2-text-dim, #888);
    font-size: 10px;
    margin-left: auto;
  }

  .store-btn {
    padding: 2px 8px;
    border: 1px solid var(--v2-accent-cyan, #00d4ff);
    border-radius: 3px;
    background: transparent;
    color: var(--v2-accent-cyan, #00d4ff);
    font-family: 'Roboto Mono', monospace;
    font-size: 10px;
    font-weight: 700;
    cursor: pointer;
    transition: background 0.15s;
  }

  .store-btn:hover {
    background: rgba(0, 212, 255, 0.12);
  }

  .store-bar {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 4px 0;
    border-bottom: 1px solid var(--v2-border, #333);
  }

  .store-label {
    display: flex;
    align-items: center;
    gap: 4px;
    color: var(--v2-text-dim, #888);
    font-size: 10px;
  }

  .store-input {
    width: 40px;
    padding: 1px 4px;
    border: 1px solid var(--v2-border, #444);
    border-radius: 2px;
    background: var(--v2-bg-input, #1a1a2e);
    color: var(--v2-text-primary, #eee);
    font-family: 'Roboto Mono', monospace;
    font-size: 10px;
    text-align: center;
  }

  .channel-list {
    overflow-y: auto;
    overflow-x: hidden;
    scrollbar-width: thin;
    scrollbar-color: var(--v2-border, #444) transparent;
  }

  .channel-row {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 3px 4px;
    border-bottom: 1px solid rgba(255, 255, 255, 0.04);
    transition: background 0.1s;
    min-height: 22px;
  }

  .channel-row:hover {
    background: rgba(255, 255, 255, 0.04);
  }

  .channel-row.selected {
    background: rgba(0, 212, 255, 0.08);
    border-left: 2px solid var(--v2-accent-cyan, #00d4ff);
    padding-left: 2px;
  }

  .channel-row.empty {
    opacity: 0.4;
  }

  .channel-row.empty:hover {
    opacity: 0.7;
  }

  .ch-number {
    color: var(--v2-text-dim, #888);
    font-size: 10px;
    font-weight: 700;
    min-width: 18px;
    text-align: right;
    flex-shrink: 0;
  }

  .ch-freq {
    color: var(--v2-text-primary, #eee);
    font-size: 11px;
    font-weight: 600;
    min-width: 80px;
    flex-shrink: 0;
    letter-spacing: 0.02em;
  }

  .ch-mode {
    color: var(--v2-accent-cyan, #00d4ff);
    font-size: 10px;
    font-weight: 700;
    min-width: 36px;
    flex-shrink: 0;
    text-transform: uppercase;
  }

  .ch-name {
    color: var(--v2-text-muted, #aaa);
    font-size: 10px;
    min-width: 0;
    flex: 1 1 auto;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    background: none;
    border: none;
    font-family: 'Roboto Mono', monospace;
    padding: 0;
    cursor: pointer;
    text-align: left;
  }

  .ch-name:hover {
    color: var(--v2-text-primary, #eee);
    text-decoration: underline;
    text-decoration-style: dotted;
  }

  .ch-name-input {
    flex: 1 1 auto;
    min-width: 0;
    padding: 0 2px;
    border: 1px solid var(--v2-accent-cyan, #00d4ff);
    border-radius: 2px;
    background: var(--v2-bg-input, #1a1a2e);
    color: var(--v2-text-primary, #eee);
    font-family: 'Roboto Mono', monospace;
    font-size: 10px;
    outline: none;
  }

  .ch-empty-label {
    color: var(--v2-text-dim, #555);
    font-size: 10px;
    font-style: italic;
    flex: 1;
  }

  .ch-actions {
    display: flex;
    gap: 3px;
    flex-shrink: 0;
    margin-left: auto;
  }

  .action-btn {
    padding: 1px 5px;
    border: 1px solid var(--v2-border, #444);
    border-radius: 2px;
    background: transparent;
    font-family: 'Roboto Mono', monospace;
    font-size: 9px;
    font-weight: 700;
    cursor: pointer;
    transition: all 0.15s;
    white-space: nowrap;
  }

  .recall-btn {
    color: var(--v2-accent-green, #4ade80);
    border-color: var(--v2-accent-green, #4ade80);
  }

  .recall-btn:hover {
    background: rgba(74, 222, 128, 0.15);
  }

  .clear-btn {
    color: var(--v2-text-dim, #888);
    border-color: var(--v2-border, #444);
  }

  .clear-btn:hover {
    color: var(--v2-accent-red, #ef4444);
    border-color: var(--v2-accent-red, #ef4444);
  }

  .clear-confirm {
    color: var(--v2-accent-red, #ef4444);
    border-color: var(--v2-accent-red, #ef4444);
  }

  .clear-confirm:hover {
    background: rgba(239, 68, 68, 0.2);
  }

  .cancel-btn {
    color: var(--v2-text-dim, #888);
  }

  .store-confirm {
    color: var(--v2-accent-cyan, #00d4ff);
    border-color: var(--v2-accent-cyan, #00d4ff);
  }

  .store-confirm:hover {
    background: rgba(0, 212, 255, 0.12);
  }

  .store-btn-inline {
    color: var(--v2-accent-cyan, #00d4ff);
    border-color: var(--v2-border, #444);
  }

  .store-btn-inline:hover {
    border-color: var(--v2-accent-cyan, #00d4ff);
    background: rgba(0, 212, 255, 0.08);
  }

  .empty-state {
    color: var(--v2-text-dim, #666);
    font-size: 10px;
    text-align: center;
    padding: 16px 8px;
    font-style: italic;
  }
</style>
