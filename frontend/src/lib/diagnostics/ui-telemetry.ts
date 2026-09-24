import type { ManagedTxState } from '$lib/runtime/tx-controller/managed-state';

/** Temporary, build-gated aggregate UI telemetry for MOR-2551. */
export const UI_TELEMETRY_ENABLED = import.meta.env.VITE_UI_DIAGNOSTICS === '1';

type Counter = { count: number; bytes: number; durationMs: number; maxMs: number };
type Metric =
  | 'controlText' | 'audioRx' | 'hardwareArrival' | 'audioFftArrival'
  | 'radioCommit' | 'hardwareAccepted' | 'audioFftAccepted'
  | 'scopePublish' | 'spectrumDraw' | 'waterfallFrameWork' | 'audioSpectrumDraw'
  | 'controlParse' | 'stateApply' | 'stateEnvelopeValidation'
  | 'stateStoreCommit' | 'stateStoreAccepted'
  | 'txAppAuthorityEffect' | 'txAppStateRefresh' | 'txAppOtherRefresh'
  | 'txHostSessionRefresh' | 'txHostRefreshStarted' | 'txHostRefreshQueued'
  | 'txControllerRefresh' | 'txWsAuthorityRefresh' | 'txOnCycleRefresh'
  | 'txPresentationTick' | 'txProjection'
  | 'txPublicationAttempt' | 'txPublicationEqual' | 'txPublicationChanged'
  | 'txGetStarted' | 'txGetCompleted' | 'txGetFailed' | 'txGetDiscarded'
  | 'txGetHttpLatency' | 'radioViewCompute';

type ControlCategory = 'stateFull' | 'stateDelta' | 'stateOther' | 'response'
  | 'error' | 'notification' | 'event' | 'commandLifecycle' | 'other' | 'malformed';
type StateKey = 'fieldStatus' | 'main' | 'sub' | 'active' | 'connection'
  | 'radioHealth' | 'ptt' | 'txTarget';
const stateKeys: StateKey[] = [
  'fieldStatus', 'main', 'sub', 'active', 'connection', 'radioHealth', 'ptt', 'txTarget',
];
const controlCategories: Record<ControlCategory, { count: number; units: number }> = {
  stateFull: { count: 0, units: 0 }, stateDelta: { count: 0, units: 0 },
  stateOther: { count: 0, units: 0 }, response: { count: 0, units: 0 },
  error: { count: 0, units: 0 }, notification: { count: 0, units: 0 },
  event: { count: 0, units: 0 }, commandLifecycle: { count: 0, units: 0 },
  other: { count: 0, units: 0 }, malformed: { count: 0, units: 0 },
};
const deltaKeyPresence = Object.fromEntries(stateKeys.map((key) => [key, 0])) as Record<StateKey, number>;
let deltaChangedKeyCount = 0;
let deltaOtherKeyCount = 0;
let deltaFieldStatusEntries = 0;

const metrics: Record<Metric, Counter> = {
  controlText: { count: 0, bytes: 0, durationMs: 0, maxMs: 0 },
  audioRx: { count: 0, bytes: 0, durationMs: 0, maxMs: 0 },
  hardwareArrival: { count: 0, bytes: 0, durationMs: 0, maxMs: 0 },
  audioFftArrival: { count: 0, bytes: 0, durationMs: 0, maxMs: 0 },
  radioCommit: { count: 0, bytes: 0, durationMs: 0, maxMs: 0 },
  hardwareAccepted: { count: 0, bytes: 0, durationMs: 0, maxMs: 0 },
  audioFftAccepted: { count: 0, bytes: 0, durationMs: 0, maxMs: 0 },
  scopePublish: { count: 0, bytes: 0, durationMs: 0, maxMs: 0 },
  spectrumDraw: { count: 0, bytes: 0, durationMs: 0, maxMs: 0 },
  waterfallFrameWork: { count: 0, bytes: 0, durationMs: 0, maxMs: 0 },
  audioSpectrumDraw: { count: 0, bytes: 0, durationMs: 0, maxMs: 0 },
  controlParse: { count: 0, bytes: 0, durationMs: 0, maxMs: 0 },
  stateApply: { count: 0, bytes: 0, durationMs: 0, maxMs: 0 },
  stateEnvelopeValidation: { count: 0, bytes: 0, durationMs: 0, maxMs: 0 },
  stateStoreCommit: { count: 0, bytes: 0, durationMs: 0, maxMs: 0 },
  stateStoreAccepted: { count: 0, bytes: 0, durationMs: 0, maxMs: 0 },
  txAppAuthorityEffect: { count: 0, bytes: 0, durationMs: 0, maxMs: 0 },
  txAppStateRefresh: { count: 0, bytes: 0, durationMs: 0, maxMs: 0 },
  txAppOtherRefresh: { count: 0, bytes: 0, durationMs: 0, maxMs: 0 },
  txHostSessionRefresh: { count: 0, bytes: 0, durationMs: 0, maxMs: 0 },
  txHostRefreshStarted: { count: 0, bytes: 0, durationMs: 0, maxMs: 0 },
  txHostRefreshQueued: { count: 0, bytes: 0, durationMs: 0, maxMs: 0 },
  txControllerRefresh: { count: 0, bytes: 0, durationMs: 0, maxMs: 0 },
  txWsAuthorityRefresh: { count: 0, bytes: 0, durationMs: 0, maxMs: 0 },
  txOnCycleRefresh: { count: 0, bytes: 0, durationMs: 0, maxMs: 0 },
  txPresentationTick: { count: 0, bytes: 0, durationMs: 0, maxMs: 0 },
  txProjection: { count: 0, bytes: 0, durationMs: 0, maxMs: 0 },
  txPublicationAttempt: { count: 0, bytes: 0, durationMs: 0, maxMs: 0 },
  txPublicationEqual: { count: 0, bytes: 0, durationMs: 0, maxMs: 0 },
  txPublicationChanged: { count: 0, bytes: 0, durationMs: 0, maxMs: 0 },
  txGetStarted: { count: 0, bytes: 0, durationMs: 0, maxMs: 0 },
  txGetCompleted: { count: 0, bytes: 0, durationMs: 0, maxMs: 0 },
  txGetFailed: { count: 0, bytes: 0, durationMs: 0, maxMs: 0 },
  txGetDiscarded: { count: 0, bytes: 0, durationMs: 0, maxMs: 0 },
  txGetHttpLatency: { count: 0, bytes: 0, durationMs: 0, maxMs: 0 },
  radioViewCompute: { count: 0, bytes: 0, durationMs: 0, maxMs: 0 },
};

let txGetInFlight = 0;
let txGetMaxInFlight = 0;
let startedAt = 0;
let summary: HTMLElement | null = null;

function formatSummary(): string {
  const elapsedSeconds = Math.max((performance.now() - startedAt) / 1000, 0.001);
  const lines = [`RigPlane UI diagnostics — ${elapsedSeconds.toFixed(1)} s since page startup/reset`,
    'Rates are averages since reset; binary bytes are incoming ArrayBuffer lengths.'];
  for (const [name, metric] of Object.entries(metrics)) {
    const timed = name === 'controlParse' || name === 'stateApply'
      || name === 'stateEnvelopeValidation' || name === 'stateStoreCommit'
      || name === 'txAppAuthorityEffect' || name === 'txGetHttpLatency';
    const duration = metric.count && (metric.durationMs || timed)
      ? `; total ${metric.durationMs.toFixed(1)} ms; mean ${(metric.durationMs / metric.count).toFixed(2)} ms; max ${metric.maxMs.toFixed(2)} ms`
      : '';
    const volume = timed || name === 'stateStoreAccepted'
      || name.startsWith('tx') || name === 'radioViewCompute' ? ''
      : name === 'controlText' ? `; ${metric.bytes} UTF-16 code units` : `; ${metric.bytes} B`;
    lines.push(`${name}: ${metric.count} (${(metric.count / elapsedSeconds).toFixed(1)}/s)${volume}${duration}`);
  }
  lines.push(`txGetInFlight: ${txGetInFlight}; txGetMaxInFlight: ${txGetMaxInFlight}`);
  lines.push('txAppAuthorityEffect is synchronous effect work, including JSON stringify and refresh call. txAppStateRefresh means state reference changed since prior refresh; txAppOtherRefresh covers remaining effect triggers.');
  lines.push('txHostRefreshStarted counts actual controller refresh calls; txHostRefreshQueued counts coalesced requests.');
  lines.push('txGetCompleted means current GET resolved; failed means current request error; discarded means superseded response/error. txGetHttpLatency is client-promise wall time, not JS CPU.');
  lines.push('txPublicationEqual compares every ManagedTxState field; attempts still notify every listener. txProjection counts full managed-TX projections; radioViewCompute counts uncached radio view projection attempts.');
  lines.push('Control categories (whole-envelope UTF-16 units; key-specific units unavailable):');
  const categorized = Object.values(controlCategories);
  lines.push(`categorized total: ${categorized.reduce((total, item) => total + item.count, 0)}; ${categorized.reduce((total, item) => total + item.units, 0)} UTF-16 units`);
  for (const [name, category] of Object.entries(controlCategories)) {
    lines.push(`${name}: ${category.count}; ${category.units} UTF-16 units`);
  }
  lines.push(`Delta changed top-level keys: ${deltaChangedKeyCount}; unlisted keys: ${deltaOtherKeyCount}`);
  lines.push(`Delta fieldStatus entries: ${deltaFieldStatusEntries} (entry count, not serialized size)`);
  lines.push(`Delta key presence: ${stateKeys.map((key) => `${key}=${deltaKeyPresence[key]}`).join(', ')}`);
  lines.push('stateEnvelopeValidation times isValidServerState in envelope apply; stateApply excludes that and nested setRadioState.');
  lines.push('stateStoreCommit counts control-message setter attempts (including duplicate caller call), with repeated validation, topology/revision checks, and synchronous subscribers.');
  lines.push('stateStoreAccepted counts control-message setter calls returning true; delayed capability-refresh commits and outer state-update notification are not timed.');
  lines.push('Arrival = delivered binary frame; accepted = frame stored by ScopeController.');
  lines.push('Draw times measure synchronous renderer calls, not GPU presentation.');
  lines.push('waterfallFrameWork includes frame option update and row push; excludes separate effect/resize work.');
  return lines.join('\n');
}

function installOverlay(): void {
  if (summary || !document.body) return;
  const panel = document.createElement('details');
  panel.setAttribute('aria-label', 'UI diagnostics');
  panel.style.cssText = 'position:fixed;right:8px;bottom:8px;z-index:2147483647;max-width:min(95vw,560px);max-height:70vh;overflow:auto;background:#171b21;color:#fff;border:1px solid #9ba4af;border-radius:6px;padding:7px;font:12px/1.4 monospace;box-shadow:0 2px 12px #0008';
  const heading = document.createElement('summary');
  heading.textContent = 'UI diagnostics';
  panel.append(heading);
  let paused = false;
  const pause = document.createElement('button');
  pause.type = 'button';
  pause.textContent = 'Pause updates';
  pause.style.cssText = 'margin:6px 6px 6px 0;padding:4px 8px';
  pause.onclick = () => {
    paused = !paused;
    pause.textContent = paused ? 'Resume updates' : 'Pause updates';
    if (summary) summary.textContent = formatSummary();
  };
  panel.append(pause);
  const reset = document.createElement('button');
  reset.type = 'button';
  reset.textContent = 'Reset window';
  reset.style.cssText = 'margin:6px 6px 6px 0;padding:4px 8px';
  reset.onclick = () => {
    resetUiTelemetry();
    paused = false;
    pause.textContent = 'Pause updates';
    if (summary) summary.textContent = formatSummary();
  };
  panel.append(reset);
  const copy = document.createElement('button');
  copy.type = 'button';
  copy.textContent = 'Copy summary';
  copy.style.cssText = 'margin:6px 0;padding:4px 8px';
  copy.onclick = async () => {
    try {
      await navigator.clipboard.writeText(summary?.textContent ?? formatSummary());
      copy.textContent = 'Copied';
    } catch {
      paused = true;
      pause.textContent = 'Resume updates';
      if (summary) summary.textContent = formatSummary();
      copy.textContent = 'Copy unavailable; select frozen text below';
    }
  };
  panel.append(copy);
  summary = document.createElement('pre');
  summary.style.cssText = 'white-space:pre-wrap;user-select:text;margin:4px 0';
  panel.append(summary);
  document.body.append(panel);
  summary.textContent = formatSummary();
  setInterval(() => { if (summary && !paused) summary.textContent = formatSummary(); }, 5000);
}

function start(): void {
  if (startedAt) return;
  startedAt = performance.now();
  if (document.body) installOverlay();
  else document.addEventListener('DOMContentLoaded', installOverlay, { once: true });
}

export function recordUiMetric(metric: Metric, bytes = 0, durationMs = 0): void {
  if (!UI_TELEMETRY_ENABLED) return;
  try {
    start();
    const entry = metrics[metric];
    entry.count++;
    entry.bytes += bytes;
    entry.durationMs += durationMs;
    entry.maxMs = Math.max(entry.maxMs, durationMs);
  } catch { /* Diagnostic UI failure must not interrupt radio or TX paths. */ }
}

const managedTxFields = Object.keys({
  phase: true, intent: true, radioTx: true, txRisk: true, fault: true, faultDetail: true,
  fresh: true, releaseRequired: true, configuredSeconds: true, remainingMs: true,
  lastOperation: true,
} satisfies Record<keyof ManagedTxState, true>) as (keyof ManagedTxState)[];

/** Count a completed comparison without storing any authority value. */
export function recordManagedTxPublication(before: Readonly<ManagedTxState>, after: Readonly<ManagedTxState>): void {
  if (!UI_TELEMETRY_ENABLED) return;
  recordUiMetric('txPublicationAttempt');
  try {
    recordUiMetric(managedTxFields.every((field) => Object.is(before[field], after[field]))
      ? 'txPublicationEqual' : 'txPublicationChanged');
  } catch { /* A diagnostic comparison must never suppress publication. */ }
}

export function recordManagedTxGetStart(): void {
  if (!UI_TELEMETRY_ENABLED) return;
  recordUiMetric('txGetStarted');
  txGetInFlight++;
  txGetMaxInFlight = Math.max(txGetMaxInFlight, txGetInFlight);
}

export function recordManagedTxGetFinish(outcome: 'completed' | 'failed' | 'discarded', latencyMs: number): void {
  if (!UI_TELEMETRY_ENABLED) return;
  txGetInFlight = Math.max(0, txGetInFlight - 1);
  recordUiMetric(outcome === 'completed' ? 'txGetCompleted'
    : outcome === 'failed' ? 'txGetFailed' : 'txGetDiscarded');
  recordUiMetric('txGetHttpLatency', 0, latencyMs);
}

export function recordControlEnvelope(raw: unknown, units: number): void {
  if (!UI_TELEMETRY_ENABLED) return;
  start();
  const message = raw !== null && typeof raw === 'object' && !Array.isArray(raw)
    ? raw as Record<string, unknown> : null;
  const kind = message?.type;
  let category: ControlCategory = 'other';
  if (kind === 'state_update') {
    const envelope = message?.data;
    const stateType = envelope !== null && typeof envelope === 'object' && !Array.isArray(envelope)
      ? (envelope as Record<string, unknown>).type : undefined;
    category = stateType === 'full' ? 'stateFull' : stateType === 'delta' ? 'stateDelta' : 'stateOther';
    if (category === 'stateDelta') {
      const changed = (envelope as Record<string, unknown>).changed;
      if (changed !== null && typeof changed === 'object' && !Array.isArray(changed)) {
        const keys = Object.keys(changed);
        deltaChangedKeyCount += keys.length;
        for (const key of keys) {
          if (Object.hasOwn(deltaKeyPresence, key)) {
            deltaKeyPresence[key as StateKey]++;
          } else {
            deltaOtherKeyCount++;
          }
        }
        const fieldStatus = (changed as Record<string, unknown>).fieldStatus;
        if (fieldStatus !== null && typeof fieldStatus === 'object' && !Array.isArray(fieldStatus)) {
          deltaFieldStatusEntries += Object.keys(fieldStatus).length;
        }
      }
    }
  } else if (kind === 'response' || kind === 'error' || kind === 'notification' || kind === 'event') {
    category = kind;
  } else if (kind === 'command_lifecycle') {
    category = 'commandLifecycle';
  }
  controlCategories[category].count++;
  controlCategories[category].units += units;
}

export function recordMalformedControl(units: number): void {
  if (!UI_TELEMETRY_ENABLED) return;
  start();
  controlCategories.malformed.count++;
  controlCategories.malformed.units += units;
}

export function resetUiTelemetry(): void {
  if (!UI_TELEMETRY_ENABLED) return;
  for (const metric of Object.values(metrics)) {
    metric.count = 0;
    metric.bytes = 0;
    metric.durationMs = 0;
    metric.maxMs = 0;
  }
  for (const category of Object.values(controlCategories)) {
    category.count = 0;
    category.units = 0;
  }
  for (const key of stateKeys) deltaKeyPresence[key] = 0;
  deltaChangedKeyCount = 0;
  deltaOtherKeyCount = 0;
  deltaFieldStatusEntries = 0;
  txGetMaxInFlight = txGetInFlight;
  startedAt = performance.now();
}

export function uiTelemetrySummary(): string {
  return UI_TELEMETRY_ENABLED ? formatSummary() : '';
}

// This module is loaded by the UI on startup. The build flag is replaced by
// Vite, so ordinary builds never create a timer or an overlay.
if (UI_TELEMETRY_ENABLED) start();
