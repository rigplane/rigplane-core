<script lang="ts">
  import {
    bindVfoTunerContext, getVfoHandlers,
  } from '$lib/runtime/adapters/panel-adapters';

  const vfoHandlers = getVfoHandlers();
  const vfoContext = bindVfoTunerContext();

  let { visible = $bindable(false) } = $props();

  // ── State ──
  let loaded = $state(false);
  let fetching = $state(false);
  let stations = $state<any[]>([]);
  let total = $state(0);
  let page = $state(1);
  let pages = $state(0);
  let limit = $state(100);
  let statusInfo = $state<any>(null);

  // Filters
  let searchQuery = $state('');
  let onAirOnly = $state(true);
  let selectedBand = $state('');
  let selectedLang = $state('');
  let selectedCountry = $state('');
  let sortBy = $state('freq');
  let showFavouritesOnly = $state(false);

  // Expanded row
  let expandedIdx = $state<number | null>(null);

  // Favourites (localStorage)
  let favourites = $state<Set<string>>(new Set());

  // Available filter options
  let availableBands = $state<any[]>([]);

  // Load favourites from localStorage
  if (typeof window !== 'undefined') {
    try {
      const saved = localStorage.getItem('eibi-favourites');
      if (saved) favourites = new Set(JSON.parse(saved));
    } catch { /* ignore */ }
  }

  function saveFavourites() {
    if (typeof window !== 'undefined') {
      localStorage.setItem('eibi-favourites', JSON.stringify([...favourites]));
    }
  }

  function favouriteKey(s: any): string {
    return `${s.freq_khz}:${s.station}:${s.time_str}`;
  }

  function toggleFavourite(s: any) {
    const key = favouriteKey(s);
    if (favourites.has(key)) {
      favourites.delete(key);
    } else {
      favourites.add(key);
    }
    favourites = new Set(favourites); // trigger reactivity
    saveFavourites();
  }

  function isFavourite(s: any): boolean {
    return favourites.has(favouriteKey(s));
  }

  // ── API calls ──

  async function checkStatus() {
    try {
      const resp = await fetch('/api/v1/eibi/status');
      if (resp.ok) {
        statusInfo = await resp.json();
        loaded = statusInfo.loaded;
      }
    } catch { /* ignore */ }
  }

  async function fetchEiBi() {
    fetching = true;
    try {
      const resp = await fetch('/api/v1/eibi/fetch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ force: false }),
      });
      const data = await resp.json();
      if (data.status === 'ok') {
        loaded = true;
        await loadBands();
        await loadStations();
      }
    } catch { /* ignore */ }
    fetching = false;
  }

  async function loadBands() {
    try {
      const resp = await fetch('/api/v1/eibi/bands');
      if (resp.ok) {
        const data = await resp.json();
        availableBands = data.bands ?? [];
      }
    } catch { /* ignore */ }
  }

  async function loadStations() {
    const params = new URLSearchParams();
    if (onAirOnly) params.set('on_air', 'true');
    if (selectedBand) params.set('band', selectedBand);
    if (selectedLang) params.set('lang', selectedLang);
    if (selectedCountry) params.set('country', selectedCountry);
    if (searchQuery) params.set('q', searchQuery);
    params.set('sort', sortBy);
    params.set('page', String(page));
    params.set('limit', String(limit));

    try {
      const resp = await fetch(`/api/v1/eibi/stations?${params}`);
      if (resp.ok) {
        const data = await resp.json();
        stations = data.stations ?? [];
        total = data.total ?? 0;
        pages = data.pages ?? 0;

        // Client-side favourites filter
        if (showFavouritesOnly) {
          stations = stations.filter((s: any) => isFavourite(s));
        }
      }
    } catch { /* ignore */ }
  }

  function formatFreq(khz: number): string {
    if (khz >= 1000) {
      return `${(khz / 1000).toFixed(3)} MHz`;
    }
    return `${khz.toFixed(1)} kHz`;
  }

  // ── Actions ──

  function activeTuneReceiver(
    view: ReturnType<typeof vfoContext.read>['view'],
  ): 0 | 1 | null {
    if (!view || view.activeReceiver.status !== 'known') return null;
    const activeReceiver = view.activeReceiver.receiver;
    if (view.disabledReasons.some(
      ({ field, code }) => field === `receiver.${activeReceiver}`
        && code === 'capability-unavailable',
    )) return null;
    const activeVfos = view.vfos.filter(
      (vfo) => vfo.receiver === activeReceiver && vfo.isActive,
    );
    if (activeVfos.length !== 1) return null;
    const activeVfo = activeVfos[0];
    if (!activeVfo || activeVfo.frequencyHz === null) return null;
    const { slot } = activeVfo;
    if (!(slot.kind === 'slotted' || slot.kind === 'unslotted'
      || (slot.kind === 'relative' && slot.role === 'selected'))) return null;
    return activeReceiver === 'MAIN' ? 0 : 1;
  }

  function tuneToStation(s: any): void {
    const view = vfoContext.read().view;
    const receiver = activeTuneReceiver(view);
    const freqKHz = s?.freq_khz;
    if (receiver === null || typeof freqKHz !== 'number'
      || !Number.isFinite(freqKHz) || freqKHz <= 0) return;
    const hz = Math.round(freqKHz * 1000);
    if (!Number.isSafeInteger(hz) || hz <= 0) return;
    vfoHandlers.onFreqChange(hz, receiver);
  }

  function handleRowClick(idx: number) {
    expandedIdx = expandedIdx === idx ? null : idx;
  }

  function handleRowDblClick(s: any) {
    tuneToStation(s);
  }

  function handleSearch() {
    page = 1;
    loadStations();
  }

  function handleFilterChange() {
    page = 1;
    loadStations();
  }

  function handleSort(col: string) {
    if (sortBy === col) {
      // Toggle on_air sort
      sortBy = col === 'on_air' ? 'freq' : 'on_air';
    } else {
      sortBy = col;
    }
    page = 1;
    loadStations();
  }

  function prevPage() {
    if (page > 1) { page--; loadStations(); }
  }

  function nextPage() {
    if (page < pages) { page++; loadStations(); }
  }

  function handleBackdropClick(e: MouseEvent) {
    if ((e.target as HTMLElement)?.classList?.contains('eibi-backdrop')) {
      visible = false;
    }
  }

  // Init on visible
  $effect(() => {
    if (visible) {
      checkStatus().then(() => {
        if (loaded) {
          loadBands();
          loadStations();
        }
      });
    }
  });

  // Debounced search
  let searchTimer: ReturnType<typeof setTimeout> | null = null;
  function onSearchInput() {
    if (searchTimer) clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      handleSearch();
    }, 300);
  }
</script>

{#if visible}
  <div
    class="eibi-backdrop"
    onclick={handleBackdropClick}
    onkeydown={(e) => { if (e.key === 'Escape') visible = false; }}
    role="button"
    tabindex="0"
    aria-label="Close EiBi browser"
  >
    <div class="eibi-modal">
      <div class="eibi-header">
        <div class="eibi-title">
          <span class="eibi-icon">📻</span>
          EiBi Broadcast Stations
          {#if statusInfo?.last_updated}
            <span class="eibi-meta">
              Season {statusInfo.season?.toUpperCase()} • {total.toLocaleString()} stations
            </span>
          {/if}
        </div>
        <div class="eibi-header-actions">
          <button
            class="eibi-fetch-btn"
            onclick={fetchEiBi}
            disabled={fetching}
          >
            {#if fetching}⏳ Fetching...{:else}🔄 Fetch{/if}
          </button>
          <button class="eibi-close-btn" onclick={() => (visible = false)}>✕</button>
        </div>
      </div>

      {#if !loaded}
        <div class="eibi-empty">
          <p>EiBi database not loaded.</p>
          <p>Click <strong>🔄 Fetch</strong> to download broadcast schedules from eibispace.de</p>
          <p class="eibi-note">~10,000 stations • Free data • Updates seasonally</p>
        </div>
      {:else}
        <div class="eibi-filters">
          <input
            class="eibi-search"
            type="text"
            placeholder="Search stations..."
            bind:value={searchQuery}
            oninput={onSearchInput}
          />
          <label class="eibi-toggle">
            <input type="checkbox" bind:checked={onAirOnly} onchange={handleFilterChange} />
            🟢 On-air
          </label>
          <label class="eibi-toggle">
            <input type="checkbox" bind:checked={showFavouritesOnly} onchange={handleFilterChange} />
            ⭐ Favourites
          </label>
          <select class="eibi-select" bind:value={selectedBand} onchange={handleFilterChange}>
            <option value="">All bands</option>
            {#each availableBands as b}
              <option value={b.band}>{b.band} ({b.on_air}/{b.total})</option>
            {/each}
          </select>
          {#if statusInfo?.languages?.length}
            <select class="eibi-select" bind:value={selectedLang} onchange={handleFilterChange}>
              <option value="">All languages</option>
              {#each statusInfo.languages as lang}
                <option value={lang}>{lang}</option>
              {/each}
            </select>
          {/if}
          {#if statusInfo?.countries?.length}
            <select class="eibi-select" bind:value={selectedCountry} onchange={handleFilterChange}>
              <option value="">All countries</option>
              {#each statusInfo.countries as c}
                <option value={c}>{c}</option>
              {/each}
            </select>
          {/if}
        </div>

        <div class="eibi-table-wrap">
          <table class="eibi-table">
            <thead>
              <tr>
                <th class="col-fav"></th>
                <th class="col-freq sortable" onclick={() => handleSort('freq')}>
                  Freq {sortBy === 'freq' ? '▾' : ''}
                </th>
                <th class="col-station sortable" onclick={() => handleSort('station')}>
                  Station {sortBy === 'station' ? '▾' : ''}
                </th>
                <th class="col-lang sortable" onclick={() => handleSort('language')}>
                  Lang {sortBy === 'language' ? '▾' : ''}
                </th>
                <th class="col-time">Schedule</th>
                <th class="col-target">Target</th>
                <th class="col-status sortable" onclick={() => handleSort('on_air')}>
                  📡 {sortBy === 'on_air' ? '▾' : ''}
                </th>
              </tr>
            </thead>
            <tbody>
              {#each stations as s, idx}
                <!-- svelte-ignore a11y_no_static_element_interactions -->
                <tr
                  class="station-row"
                  class:on-air={s.on_air}
                  class:expanded={expandedIdx === idx}
                  onclick={() => handleRowClick(idx)}
                  ondblclick={() => handleRowDblClick(s)}
                >
                  <td class="col-fav">
                    <button
                      class="fav-btn"
                      class:active={isFavourite(s)}
                      onclick={(e) => { e.stopPropagation(); toggleFavourite(s); }}
                    >⭐</button>
                  </td>
                  <td class="col-freq">{s.freq_khz}</td>
                  <td class="col-station">{s.station}</td>
                  <td class="col-lang">{s.language_name}</td>
                  <td class="col-time">{s.time_str} {s.days ? `(${s.days})` : ''}</td>
                  <td class="col-target">{s.target}</td>
                  <td class="col-status">{s.on_air ? '🟢' : '🔴'}</td>
                </tr>
                {#if expandedIdx === idx}
                  <tr class="detail-row">
                    <td colspan="7">
                      <div class="station-detail">
                        <div class="detail-grid">
                          <span class="detail-label">Frequency:</span>
                          <span>{formatFreq(s.freq_khz)}</span>
                          <span class="detail-label">Country:</span>
                          <span>{s.country}</span>
                          <span class="detail-label">Language:</span>
                          <span>{s.language_name} ({s.language})</span>
                          <span class="detail-label">Target area:</span>
                          <span>{s.target}</span>
                          <span class="detail-label">Band:</span>
                          <span>{s.band}</span>
                          {#if s.remarks}
                            <span class="detail-label">TX site:</span>
                            <span>{s.remarks}</span>
                          {/if}
                          <span class="detail-label">Schedule:</span>
                          <span>{s.time_str} UTC {s.days || '(daily)'}</span>
                        </div>
                        <button class="tune-btn" onclick={() => tuneToStation(s)}>
                          📻 Tune to {s.freq_khz} kHz
                        </button>
                      </div>
                    </td>
                  </tr>
                {/if}
              {/each}
            </tbody>
          </table>
        </div>

        <div class="eibi-footer">
          <span class="page-info">
            Page {page} of {pages} ({total.toLocaleString()} results)
          </span>
          <div class="page-controls">
            <button class="page-btn" onclick={prevPage} disabled={page <= 1}>◀</button>
            <button class="page-btn" onclick={nextPage} disabled={page >= pages}>▶</button>
          </div>
          <span class="eibi-credit">Data: eibispace.de (Ernst Eibl)</span>
        </div>
      {/if}
    </div>
  </div>
{/if}

<style>
  .eibi-backdrop {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.5);
    z-index: 1000;
    display: flex;
    align-items: center;
    justify-content: center;
    backdrop-filter: blur(2px);
  }

  .eibi-modal {
    background: var(--v2-bg-primary, #0f0f1a);
    border: 1px solid var(--v2-border, #2a2a3e);
    border-radius: 8px;
    width: 90vw;
    max-width: 900px;
    height: 80vh;
    display: flex;
    flex-direction: column;
    box-shadow: 0 20px 60px rgba(0, 0, 0, 0.5);
  }

  .eibi-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 12px 16px;
    border-bottom: 1px solid var(--v2-border, #2a2a3e);
    flex-shrink: 0;
  }

  .eibi-title {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 14px;
    font-weight: 600;
    color: var(--v2-text-primary, #e0e0e0);
  }

  .eibi-icon {
    font-size: 18px;
  }

  .eibi-meta {
    font-size: 11px;
    color: var(--v2-text-dim, #666);
    font-weight: 400;
  }

  .eibi-header-actions {
    display: flex;
    gap: 8px;
    align-items: center;
  }

  .eibi-fetch-btn {
    padding: 4px 10px;
    font-size: 11px;
    background: rgba(0, 212, 255, 0.1);
    border: 1px solid rgba(0, 212, 255, 0.3);
    border-radius: 4px;
    color: #00d4ff;
    cursor: pointer;
  }

  .eibi-fetch-btn:hover:not(:disabled) {
    background: rgba(0, 212, 255, 0.2);
  }

  .eibi-fetch-btn:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }

  .eibi-close-btn {
    padding: 4px 8px;
    font-size: 14px;
    background: transparent;
    border: 1px solid var(--v2-border, #2a2a3e);
    border-radius: 4px;
    color: var(--v2-text-dim, #888);
    cursor: pointer;
  }

  .eibi-close-btn:hover {
    color: #ff4444;
    border-color: rgba(255, 68, 68, 0.3);
  }

  .eibi-empty {
    flex: 1;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 8px;
    color: var(--v2-text-dim, #888);
    font-size: 13px;
  }

  .eibi-note {
    font-size: 11px;
    opacity: 0.6;
  }

  .eibi-filters {
    display: flex;
    gap: 8px;
    padding: 8px 16px;
    border-bottom: 1px solid var(--v2-border, #2a2a3e);
    flex-wrap: wrap;
    align-items: center;
    flex-shrink: 0;
  }

  .eibi-search {
    flex: 1;
    min-width: 150px;
    padding: 4px 8px;
    font-size: 12px;
    font-family: 'Roboto Mono', monospace;
    background: rgba(255, 255, 255, 0.05);
    border: 1px solid var(--v2-border, #2a2a3e);
    border-radius: 4px;
    color: var(--v2-text-primary, #e0e0e0);
    outline: none;
  }

  .eibi-search:focus {
    border-color: rgba(0, 212, 255, 0.4);
  }

  .eibi-toggle {
    display: flex;
    align-items: center;
    gap: 4px;
    font-size: 11px;
    color: var(--v2-text-dim, #888);
    cursor: pointer;
    white-space: nowrap;
  }

  .eibi-toggle input {
    cursor: pointer;
  }

  .eibi-select {
    padding: 3px 6px;
    font-size: 11px;
    font-family: 'Roboto Mono', monospace;
    background: rgba(255, 255, 255, 0.05);
    border: 1px solid var(--v2-border, #2a2a3e);
    border-radius: 4px;
    color: var(--v2-text-primary, #e0e0e0);
    outline: none;
  }

  .eibi-table-wrap {
    flex: 1;
    overflow-y: auto;
    overflow-x: hidden;
  }

  .eibi-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 11px;
    font-family: 'Roboto Mono', monospace;
  }

  .eibi-table thead {
    position: sticky;
    top: 0;
    z-index: 1;
    background: var(--v2-bg-primary, #0f0f1a);
  }

  .eibi-table th {
    padding: 6px 8px;
    text-align: left;
    font-weight: 600;
    color: var(--v2-text-dim, #888);
    border-bottom: 1px solid var(--v2-border, #2a2a3e);
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }

  .eibi-table th.sortable {
    cursor: pointer;
  }

  .eibi-table th.sortable:hover {
    color: #00d4ff;
  }

  .eibi-table td {
    padding: 5px 8px;
    border-bottom: 1px solid rgba(255, 255, 255, 0.03);
    color: var(--v2-text-primary, #ccc);
  }

  .station-row {
    cursor: pointer;
    transition: background 0.1s;
  }

  .station-row:hover {
    background: rgba(255, 255, 255, 0.03);
  }

  .station-row.on-air {
    color: var(--v2-text-primary, #e0e0e0);
  }

  .station-row:not(.on-air) {
    opacity: 0.5;
  }

  .station-row.expanded {
    background: rgba(0, 212, 255, 0.05);
  }

  .col-fav { width: 30px; text-align: center; }
  .col-freq { width: 80px; font-weight: 600; color: #00d4ff !important; }
  .col-station { min-width: 150px; }
  .col-lang { width: 80px; }
  .col-time { width: 120px; font-size: 10px; }
  .col-target { width: 60px; }
  .col-status { width: 30px; text-align: center; }

  .fav-btn {
    background: none;
    border: none;
    cursor: pointer;
    font-size: 12px;
    opacity: 0.2;
    padding: 0;
    transition: opacity 0.1s;
  }

  .fav-btn:hover {
    opacity: 0.6;
  }

  .fav-btn.active {
    opacity: 1;
  }

  .detail-row td {
    padding: 0 !important;
    background: rgba(0, 212, 255, 0.03);
  }

  .station-detail {
    padding: 10px 16px;
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 16px;
  }

  .detail-grid {
    display: grid;
    grid-template-columns: auto 1fr;
    gap: 4px 12px;
    font-size: 11px;
  }

  .detail-label {
    color: var(--v2-text-dim, #666);
    font-weight: 600;
  }

  .tune-btn {
    padding: 8px 16px;
    font-size: 12px;
    font-family: 'Roboto Mono', monospace;
    background: rgba(0, 212, 255, 0.15);
    border: 1px solid rgba(0, 212, 255, 0.4);
    border-radius: 6px;
    color: #00d4ff;
    cursor: pointer;
    white-space: nowrap;
    flex-shrink: 0;
  }

  .tune-btn:hover {
    background: rgba(0, 212, 255, 0.25);
  }

  .eibi-footer {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 8px 16px;
    border-top: 1px solid var(--v2-border, #2a2a3e);
    flex-shrink: 0;
  }

  .page-info {
    font-size: 11px;
    color: var(--v2-text-dim, #666);
  }

  .page-controls {
    display: flex;
    gap: 4px;
  }

  .page-btn {
    padding: 3px 8px;
    font-size: 11px;
    background: transparent;
    border: 1px solid var(--v2-border, #2a2a3e);
    border-radius: 3px;
    color: var(--v2-text-dim, #888);
    cursor: pointer;
  }

  .page-btn:hover:not(:disabled) {
    color: #00d4ff;
    border-color: rgba(0, 212, 255, 0.3);
  }

  .page-btn:disabled {
    opacity: 0.3;
    cursor: not-allowed;
  }

  .eibi-credit {
    font-size: 9px;
    color: var(--v2-text-dim, #444);
  }
</style>
