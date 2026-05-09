<script lang="ts">
  import { Radio, Cable, Activity, Volume2, ArrowDownUp, Power, Unplug, Palette, Monitor, Tv, Settings, Bug } from 'lucide-svelte';
  import ThemePicker from '../controls/ThemePicker.svelte';
  import SendReportDialog from '../dialogs/SendReportDialog.svelte';
  import { runtime } from '$lib/runtime';
  import {
    getRadioStatus,
    getConnectionStatus,
    isScopeConnected,
    isAudioConnected,
    getHttpConnected,
    getRadioPowerOn,
    getRigConnected,
    getRadioHealth,
  } from '$lib/stores/connection.svelte';
  import { getFrequency } from '$lib/stores/radio.svelte';
  import { hasAnyScope, hasAudio, hasSpectrum } from '$lib/stores/capabilities.svelte';
  import { getLayoutMode, setLayoutMode, type LayoutMode } from '$lib/stores/layout.svelte';

  interface Props {
    onSettings?: () => void;
  }
  let { onSettings }: Props = $props();

  // Canonicalize legacy 'lcd' to 'lcd-cockpit' for UI binding — the persisted
  // value may still be 'lcd' (from pre-#889 installs), but the dropdown only
  // exposes 'lcd-cockpit' / 'lcd-scope'. Without normalization the <select>
  // would show no highlighted option for legacy users (codex P2 on PR #913).
  let layoutMode = $derived.by<LayoutMode>(() => {
    const raw = getLayoutMode();
    return raw === 'lcd' ? 'lcd-cockpit' : raw;
  });
  // Skin-switcher dropdown options. `lcd` is a legacy persisted value that
  // normalizes to `lcd-cockpit` above (kept for backward compat). Twin-skin
  // variants (`lcd-cockpit`, `lcd-scope`) are selectable per #887 / #889.
  const skinOptions: Array<{ value: LayoutMode; label: string }> = [
    { value: 'auto', label: 'AUTO' },
    { value: 'standard', label: 'Standard' },
    { value: 'lcd-cockpit', label: 'LCD Cockpit' },
    { value: 'lcd-scope', label: 'LCD Scope' },
    { value: 'sdr-test', label: 'SDR Screen (test)' },
  ];

  function handleSkinChange(ev: Event) {
    const value = (ev.currentTarget as HTMLSelectElement).value as LayoutMode;
    setLayoutMode(value);
  }

  let radioPowerOn = $derived(getRadioPowerOn());
  let isPoweredOff = $derived(radioPowerOn === false);

  let powerTooltip = $derived(
    radioPowerOn === true
      ? 'Toggle power (radio is ON — click to power off)'
      : radioPowerOn === false
        ? 'Toggle power (radio is OFF — click to power on)'
        : 'Toggle power (radio state unknown — click to toggle)'
  );

  // When radio is powered off, override statuses that depend on the radio
  let radioState = $derived(isPoweredOff ? 'disconnected' : getRadioStatus());
  let controlState = $derived(getConnectionStatus()); // server link — always real
  let scopeState = $derived(isPoweredOff ? 'disconnected' : (isScopeConnected() ? 'connected' : 'disconnected'));
  let audioState = $derived(isPoweredOff ? 'disconnected' : (isAudioConnected() ? 'connected' : 'disconnected'));
  let httpState = $derived(getHttpConnected() ? 'connected' : 'disconnected'); // server link — always real
  let rigConnected = $derived(getRigConnected());
  let radioHealth = $derived(getRadioHealth());
  // Effective radio indicator: downgrade to 'disconnected' when rigCtld reports radio offline
  let radioIndicatorState = $derived.by(() => {
    if (radioHealth?.likelyCause === 'radio_network_lost' || radioHealth?.likelyCause === 'radio_powered_off_likely') {
      return 'disconnected';
    }
    if (radioHealth?.readiness === 'delayed' || radioHealth?.readiness === 'stalled') {
      return 'degraded';
    }
    return radioState === 'connected' && !rigConnected ? 'degraded' : radioState;
  });
  let radioHealthLabel = $derived.by(() => {
    switch (radioHealth?.likelyCause) {
      case 'radio_network_lost':
        return 'radio link lost';
      case 'radio_not_responding':
        return radioHealth.readiness === 'delayed' ? 'radio response delayed' : 'radio not responding';
      case 'radio_powered_off_likely':
        return 'radio appears powered off or unreachable';
      case 'server_unreachable':
        return 'server unreachable';
      default:
        return !rigConnected && radioState === 'connected' ? 'rig offline' : '';
    }
  });

  let connectionTooltip = $derived(
    controlState === 'connected'
      ? 'Disconnect from radio (control link active)'
      : 'Connect to radio (control link inactive)'
  );

  function stateColor(state: string): string {
    switch (state) {
      case 'connected':
        return 'var(--v2-accent-green, #4ade80)';
      case 'connecting':
      case 'reconnecting':
      case 'partial':
      case 'degraded':
        return 'var(--v2-accent-yellow, #facc15)';
      case 'disconnected':
        return 'var(--v2-accent-red, #ef4444)';
      default:
        return 'var(--v2-text-dim, #666)';
    }
  }

  function handleConnectionToggle() {
    const isConnected = controlState === 'connected';
    const action = isConnected ? 'Disconnect' : 'Connect';
    if (!confirm(`${action}?`)) return;
    if (isConnected) {
      runtime.system.disconnect();
    } else {
      runtime.system.connect();
    }
  }

  async function handlePowerToggle() {
    if (radioPowerOn === true) {
      if (!confirm('Turn OFF the radio?')) return;
      try {
        await runtime.system.powerOff();
      } catch (err) {
        alert(`Failed to turn off radio: ${err}`);
      }
    } else {
      if (!confirm('Turn ON the radio?')) return;
      try {
        await runtime.system.powerOn();
      } catch (err) {
        alert(`Failed to turn on radio: ${err}`);
      }
    }
  }

  // ── Send-Report dialog (issue #1397) ──
  let reportOpen = $state(false);

  // ── Now Playing (EiBi identification) ──
  let nowPlaying = $state<any>(null);
  let nowPlayingExpanded = $state(false);
  let identifyTimer: ReturnType<typeof setTimeout> | null = null;
  let lastIdentifiedFreq = 0;

  // Poll frequency and identify station
  $effect(() => {
    const freq = getFrequency();
    if (!freq || Math.abs(freq - lastIdentifiedFreq) < 500) return;

    // Debounce: wait 800ms after freq stops changing
    // Don't update while popup is expanded (prevents flicker)
    if (identifyTimer) clearTimeout(identifyTimer);
    identifyTimer = setTimeout(async () => {
      if (nowPlayingExpanded) return;
      lastIdentifiedFreq = freq;
      const result = await runtime.system.identifyFrequency(freq);
      nowPlaying = result?.stations?.length ? result.stations[0] : null;
    }, 800);

    return () => {
      if (identifyTimer) clearTimeout(identifyTimer);
    };
  });

</script>

{#if controlState === 'disconnected'}
  <div class="control-link-lost">Control link lost</div>
{/if}
<div class="status-bar">
  <div class="status-indicators">
    <span class="indicator" role="status" title="Radio ↔ Server: {radioState}{radioHealthLabel ? ` (${radioHealthLabel})` : ''}" style="--indicator-color: {stateColor(radioIndicatorState)}">
      <span class="indicator-dot"></span>
      <Radio size={12} color="currentColor" strokeWidth={2.5} />
    </span>
    <span class="indicator" role="status" title="Control WebSocket: {controlState}" style="--indicator-color: {stateColor(controlState)}">
      <span class="indicator-dot"></span>
      <Cable size={12} color="currentColor" strokeWidth={2.5} />
    </span>
    {#if hasAnyScope()}
      <span class="indicator" role="status" title="Scope WebSocket: {scopeState}" style="--indicator-color: {stateColor(scopeState)}">
        <span class="indicator-dot"></span>
        <Activity size={12} color="currentColor" strokeWidth={2.5} />
      </span>
    {/if}
    {#if hasAudio()}
      <span class="indicator" role="status" title="Audio WebSocket: {audioState}" style="--indicator-color: {stateColor(audioState)}">
        <span class="indicator-dot"></span>
        <Volume2 size={12} color="currentColor" strokeWidth={2.5} />
      </span>
    {/if}
    <span class="indicator" role="status" title="State HTTP: {httpState}" style="--indicator-color: {stateColor(httpState)}">
      <span class="indicator-dot"></span>
      <ArrowDownUp size={12} color="currentColor" strokeWidth={2.5} />
      {#if httpState === 'disconnected'}
        <span class="http-lost-label">offline</span>
      {/if}
    </span>
  </div>

  <div class="status-info">
    {#if nowPlaying}
      <button type="button" class="now-playing" onclick={() => (nowPlayingExpanded = !nowPlayingExpanded)} onkeydown={(e) => { if (e.key === 'Escape') nowPlayingExpanded = false; }} aria-expanded={nowPlayingExpanded} aria-haspopup="dialog">
        <span class="np-icon">📻</span>
        <span class="np-station">{nowPlaying.station}</span>
        <span class="np-lang">{nowPlaying.city ? `${nowPlaying.city}, ${nowPlaying.state}` : nowPlaying.language_name}</span>
        {#if nowPlaying.on_air}<span class="np-live">LIVE</span>{/if}
      </button>
      {#if nowPlayingExpanded}
        <!-- svelte-ignore a11y_no_static_element_interactions -->
        <div class="np-backdrop" onclick={() => (nowPlayingExpanded = false)} onkeydown={(e) => { if (e.key === 'Escape') nowPlayingExpanded = false; }}>
          <!-- svelte-ignore a11y_no_static_element_interactions -->
          <div class="np-detail" role="dialog" tabindex="-1" aria-modal="true" aria-label="Station details" onclick={(e) => e.stopPropagation()} onkeydown={(e) => { if (e.key === 'Escape') { e.stopPropagation(); nowPlayingExpanded = false; } }}>
            <div class="np-detail-header">
              <span>📻 {nowPlaying.station}</span>
              <button class="np-close" onclick={() => (nowPlayingExpanded = false)}>✕</button>
            </div>
            <div class="np-detail-grid">
              <span class="np-label">Frequency:</span><span>{nowPlaying.freq_khz} kHz</span>
              {#if nowPlaying.city}
                <span class="np-label">Location:</span><span>{nowPlaying.city}, {nowPlaying.state}</span>
              {/if}
              <span class="np-label">Language:</span><span>{nowPlaying.language_name}</span>
              {#if !nowPlaying.city}
                <span class="np-label">Country:</span><span>{nowPlaying.country}</span>
                <span class="np-label">Target:</span><span>{nowPlaying.target}</span>
              {/if}
              {#if nowPlaying.time_str !== 'local'}
                <span class="np-label">Schedule:</span><span>{nowPlaying.time_str} UTC {nowPlaying.days || '(daily)'}</span>
              {/if}
              <span class="np-label">Band:</span><span>{nowPlaying.band}</span>
              {#if nowPlaying.remarks}
                <span class="np-label">Details:</span><span>{nowPlaying.remarks}</span>
              {/if}
              {#if nowPlaying.source}
                <span class="np-label">Source:</span><span class="np-source">{nowPlaying.source}</span>
              {/if}
            </div>
          </div>
        </div>
      {/if}
    {/if}
  </div>

  <div class="status-controls">
    <button
      type="button"
      class="control-btn report-btn"
      onclick={() => (reportOpen = true)}
      title="Send diagnostic report"
      aria-label="Send diagnostic report"
    >
      <Bug size={14} strokeWidth={2} />
      <span class="btn-label">Report</span>
    </button>
    {#if onSettings}
      <button
        type="button"
        class="control-btn settings-btn"
        onclick={onSettings}
        title="Show settings"
        aria-label="Show settings"
      >
        <Settings size={14} strokeWidth={2} />
      </button>
    {/if}
    <label class="skin-switcher" title="Select UI skin">
      {#if layoutMode === 'lcd-cockpit' || layoutMode === 'lcd-scope'}
        <Tv size={14} strokeWidth={2} aria-hidden="true" />
      {:else}
        <Monitor size={14} strokeWidth={2} aria-hidden="true" />
      {/if}
      <span class="sr-only">Skin</span>
      <select
        class="skin-select"
        aria-label="Select UI skin"
        value={layoutMode}
        onchange={handleSkinChange}
      >
        {#each skinOptions as opt (opt.value)}
          <option value={opt.value}>{opt.label}</option>
        {/each}
      </select>
    </label>
    <ThemePicker />
    <button
      type="button"
      class="control-btn"
      onclick={handleConnectionToggle}
      title={connectionTooltip}
    >
      <Unplug size={14} strokeWidth={2} />
      <span class="btn-label">{controlState === 'connected' ? 'Disconnect' : 'Connect'}</span>
    </button>
    <button
      type="button"
      class="control-btn power-toggle-btn"
      class:is-on={radioPowerOn === true}
      onclick={handlePowerToggle}
      title={powerTooltip}
    >
      <Power size={14} strokeWidth={2} />
      <span class="btn-label">{radioPowerOn === true ? 'OFF' : 'ON'}</span>
    </button>
  </div>
</div>

<SendReportDialog open={reportOpen} onClose={() => (reportOpen = false)} />

<style>
  .control-link-lost {
    background: var(--v2-accent-red, #ef4444);
    color: #fff;
    font-family: 'Roboto Mono', monospace;
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    text-align: center;
    padding: 2px 0;
    user-select: none;
  }

  .http-lost-label {
    font-size: 9px;
    color: var(--v2-accent-red, #ef4444);
    font-weight: 700;
    margin-left: 2px;
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }

  .status-bar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    height: 28px;
    padding: 0 10px;
    background: var(--v2-bg-darkest, #0a0a0f);
    border-bottom: 1px solid var(--v2-border-darker, #1a1a2e);
    font-family: 'Roboto Mono', monospace;
    font-size: 11px;
    color: var(--v2-text-primary, #fff);
    user-select: none;
  }

  .status-indicators {
    display: flex;
    align-items: center;
    gap: 12px;
  }

  .indicator {
    position: relative;
    display: flex;
    align-items: center;
    cursor: pointer;
    color: var(--indicator-color);
    transition: transform 0.15s;
  }

  .indicator:hover {
    transform: scale(1.15);
  }

  .indicator-dot {
    position: absolute;
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--indicator-color);
    box-shadow: 0 0 8px var(--indicator-color);
    top: -2px;
    right: -2px;
    pointer-events: none;
  }

  .status-info {
    display: flex;
    align-items: center;
    gap: 16px;
  }

  .status-controls {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .control-btn {
    display: flex;
    align-items: center;
    gap: 4px;
    padding: 4px 8px;
    background: var(--v2-bg-input, #1a1a2e);
    border: 1px solid var(--v2-border, #2a2a3e);
    border-radius: 3px;
    color: var(--v2-text-primary, #fff);
    cursor: pointer;
    transition: all 0.15s ease;
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }

  .btn-label {
    white-space: nowrap;
  }

  /* Skin switcher (replaces the old STD/LCD cycle button) */
  .skin-switcher {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 3px 6px 3px 8px;
    background: var(--v2-bg-input, #1a1a2e);
    border: 1px solid var(--v2-border, #2a2a3e);
    border-radius: 3px;
    color: var(--v2-text-primary, #fff);
    transition: all 0.15s ease;
    cursor: pointer;
  }

  .skin-switcher:hover,
  .skin-switcher:focus-within {
    background: var(--v2-bg-card, #252540);
    border-color: var(--v2-accent-cyan, #06b6d4);
  }

  .skin-select {
    appearance: none;
    background: transparent;
    border: none;
    color: inherit;
    font: inherit;
    font-family: 'Roboto Mono', monospace;
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    padding: 0 14px 0 2px;
    cursor: pointer;
    background-image: linear-gradient(45deg, transparent 50%, currentColor 50%),
      linear-gradient(135deg, currentColor 50%, transparent 50%);
    background-position: calc(100% - 7px) 50%, calc(100% - 3px) 50%;
    background-size: 4px 4px;
    background-repeat: no-repeat;
  }

  .skin-select:focus {
    outline: none;
  }

  .skin-select option {
    background: var(--v2-bg-input, #1a1a2e);
    color: var(--v2-text-primary, #fff);
  }

  .sr-only {
    position: absolute;
    width: 1px;
    height: 1px;
    padding: 0;
    margin: -1px;
    overflow: hidden;
    clip: rect(0, 0, 0, 0);
    white-space: nowrap;
    border: 0;
  }

  .control-btn:hover {
    background: var(--v2-bg-card, #252540);
    border-color: var(--v2-accent-cyan, #06b6d4);
    color: var(--v2-text-primary, #fff);
  }

  .control-btn:active {
    transform: scale(0.95);
  }

  .control-btn:disabled {
    opacity: 0.4;
    cursor: not-allowed;
    pointer-events: none;
  }

  .power-toggle-btn {
    border-color: var(--v2-accent-green, #4ade80);
    color: var(--v2-accent-green, #4ade80);
  }

  .power-toggle-btn:hover {
    border-color: var(--v2-accent-green, #4ade80);
    background: rgba(74, 222, 128, 0.1);
  }

  .power-toggle-btn.is-on {
    border-color: var(--v2-accent-red, #ef4444);
    color: var(--v2-accent-red, #ef4444);
  }

  .power-toggle-btn.is-on:hover {
    border-color: var(--v2-accent-red, #ef4444);
    background: rgba(239, 68, 68, 0.1);
  }

  /* Now Playing badge */
  .status-info {
    position: relative;
  }

  .now-playing {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 2px 8px;
    background: rgba(192, 132, 252, 0.08);
    border: 1px solid rgba(192, 132, 252, 0.2);
    border-radius: 4px;
    cursor: pointer;
    max-width: 350px;
    overflow: hidden;
    transition: background 0.15s;
    font-family: 'Roboto Mono', monospace;
    color: inherit;
  }

  .now-playing:hover {
    background: rgba(192, 132, 252, 0.15);
    border-color: rgba(192, 132, 252, 0.4);
  }

  .np-icon {
    font-size: 11px;
    flex-shrink: 0;
  }

  .np-station {
    font-size: 11px;
    font-weight: 600;
    color: var(--v2-text-primary, #e0e0e0);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .np-lang {
    font-size: 9px;
    color: var(--v2-text-dim, #888);
    white-space: nowrap;
  }

  .np-live {
    font-size: 8px;
    font-weight: 700;
    color: #4ade80;
    background: rgba(74, 222, 128, 0.15);
    padding: 1px 4px;
    border-radius: 2px;
    letter-spacing: 0.05em;
    flex-shrink: 0;
  }

  .np-backdrop {
    position: fixed;
    inset: 0;
    z-index: 999;
  }

  .np-detail {
    position: fixed;
    top: 36px;
    left: 50%;
    transform: translateX(-50%);
    background: var(--v2-bg-primary, #0f0f1a);
    border: 1px solid rgba(192, 132, 252, 0.4);
    border-radius: 8px;
    padding: 0;
    box-shadow: 0 12px 40px rgba(0, 0, 0, 0.6);
    z-index: 1000;
    min-width: 280px;
    max-width: 400px;
  }

  .np-detail-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 8px 12px;
    border-bottom: 1px solid rgba(192, 132, 252, 0.2);
    font-size: 13px;
    font-weight: 600;
    color: #C084FC;
  }

  .np-close {
    background: none;
    border: none;
    color: var(--v2-text-dim, #666);
    cursor: pointer;
    font-size: 14px;
    padding: 0 2px;
  }

  .np-close:hover {
    color: #ff4444;
  }

  .np-source {
    font-size: 9px;
    color: var(--v2-text-dim, #555);
  }

  .np-detail-grid {
    display: grid;
    grid-template-columns: auto 1fr;
    gap: 4px 12px;
    padding: 10px 12px;
    font-size: 11px;
    color: var(--v2-text-primary, #ccc);
  }

  .np-label {
    color: var(--v2-text-dim, #666);
    font-weight: 600;
  }
</style>
