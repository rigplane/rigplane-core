<script lang="ts">
  import { onMount } from 'svelte';
  import { getAudioRoutingHandlers } from '$lib/runtime/adapters/panel-adapters';
  import { getReceiverLabel } from '$lib/runtime/adapters/capabilities-adapter';

  type AudioFocus = 'main' | 'sub' | 'both';

  const handlers = getAudioRoutingHandlers();

  let focus: AudioFocus = $state('both');
  let splitStereo = $state(false);
  let mainGainDb = $state(0);
  let subGainDb = $state(0);

  onMount(() => {
    const restored = handlers.restoreFromStorage();
    focus = restored.focus;
    splitStereo = restored.split_stereo;
    mainGainDb = restored.main_gain_db;
    subGainDb = restored.sub_gain_db;
  });

  function setFocus(next: AudioFocus) {
    focus = next;
    handlers.onFocusChange(next);
  }

  function toggleSplit() {
    splitStereo = !splitStereo;
    handlers.onSplitStereoChange(splitStereo);
  }

  function updateGain(channel: 'main' | 'sub', value: number) {
    if (channel === 'main') mainGainDb = value;
    else subGainDb = value;
    handlers.onChannelGainChange(channel, value);
  }

  const FOCUS_OPTIONS: Array<{ value: AudioFocus; label: string }> = [
    { value: 'main', label: getReceiverLabel('MAIN') },
    { value: 'sub', label: getReceiverLabel('SUB') },
    { value: 'both', label: 'Both' },
  ];
</script>

<div class="audio-routing" aria-label="Audio routing">
  <div class="focus-row" role="radiogroup" aria-label="Audio focus">
    {#each FOCUS_OPTIONS as opt (opt.value)}
      <button
        type="button"
        role="radio"
        aria-checked={focus === opt.value}
        class:active={focus === opt.value}
        onclick={() => setFocus(opt.value)}
      >
        {opt.label}
      </button>
    {/each}
  </div>

  <button
    type="button"
    class="split-toggle"
    class:active={splitStereo}
    aria-pressed={splitStereo}
    onclick={toggleSplit}
    title="Route MAIN to left channel, SUB to right channel"
  >
    Stereo split
  </button>

  <label class="gain-row">
    <span class="gain-label">{getReceiverLabel('MAIN')}</span>
    <input
      type="range"
      min="-60"
      max="12"
      step="1"
      aria-label="MAIN gain in decibels"
      value={mainGainDb}
      oninput={(e) => updateGain('main', Number((e.target as HTMLInputElement).value))}
    />
    <span class="gain-value">{mainGainDb} dB</span>
  </label>

  <label class="gain-row">
    <span class="gain-label">{getReceiverLabel('SUB')}</span>
    <input
      type="range"
      min="-60"
      max="12"
      step="1"
      aria-label="SUB gain in decibels"
      value={subGainDb}
      oninput={(e) => updateGain('sub', Number((e.target as HTMLInputElement).value))}
    />
    <span class="gain-value">{subGainDb} dB</span>
  </label>
</div>

<style>
  .audio-routing {
    display: flex;
    flex-direction: column;
    gap: 6px;
    padding: 7px 8px;
    border-top: 1px solid var(--v2-border-subtle, rgba(255, 255, 255, 0.08));
  }
  .focus-row {
    display: flex;
    gap: 4px;
  }
  .focus-row > button {
    flex: 1 1 0;
    min-width: 0;
    padding: 4px 6px;
    background: transparent;
    border: 1px solid var(--v2-border-subtle, rgba(255, 255, 255, 0.15));
    color: var(--v2-text-primary, #e5e7eb);
    border-radius: 3px;
    font-size: 10px;
    font-family: inherit;
    cursor: pointer;
  }
  .focus-row > button.active {
    background: var(--v2-accent-cyan-alt, rgba(34, 211, 238, 0.22));
    border-color: var(--v2-accent-cyan, #22d3ee);
  }
  .split-toggle {
    padding: 4px 8px;
    background: transparent;
    border: 1px solid var(--v2-border-subtle, rgba(255, 255, 255, 0.15));
    color: var(--v2-text-primary, #e5e7eb);
    border-radius: 3px;
    font-size: 10px;
    font-family: inherit;
    cursor: pointer;
  }
  .split-toggle.active {
    background: var(--v2-accent-cyan-alt, rgba(34, 211, 238, 0.22));
    border-color: var(--v2-accent-cyan, #22d3ee);
  }
  .gain-row {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 10px;
  }
  .gain-label {
    flex: 0 0 48px;
    color: var(--v2-text-muted, #9ca3af);
  }
  .gain-row input {
    flex: 1 1 0;
  }
  .gain-value {
    flex: 0 0 42px;
    text-align: right;
    font-family: 'Roboto Mono', monospace;
    color: var(--v2-text-primary, #e5e7eb);
  }
</style>
