/** Temporary, build-gated aggregate UI telemetry for MOR-2551. */
export const UI_TELEMETRY_ENABLED = import.meta.env.VITE_UI_DIAGNOSTICS === '1';

type Counter = { count: number; bytes: number; durationMs: number; maxMs: number };
type Metric =
  | 'controlText' | 'audioRx' | 'hardwareArrival' | 'audioFftArrival'
  | 'radioCommit' | 'hardwareAccepted' | 'audioFftAccepted'
  | 'scopePublish' | 'spectrumDraw' | 'waterfallDraw' | 'audioSpectrumDraw';

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
  waterfallDraw: { count: 0, bytes: 0, durationMs: 0, maxMs: 0 },
  audioSpectrumDraw: { count: 0, bytes: 0, durationMs: 0, maxMs: 0 },
};

let startedAt = 0;
let summary: HTMLElement | null = null;
let textEncoder: TextEncoder | null = null;

function formatSummary(): string {
  const elapsedSeconds = Math.max((performance.now() - startedAt) / 1000, 0.001);
  const lines = [`RigPlane UI diagnostics — ${elapsedSeconds.toFixed(1)} s since page startup`,
    'Rates are whole-session averages; bytes are incoming wire bytes.'];
  for (const [name, metric] of Object.entries(metrics)) {
    const duration = metric.durationMs
      ? `; total ${metric.durationMs.toFixed(1)} ms; mean ${(metric.durationMs / metric.count).toFixed(2)} ms; max ${metric.maxMs.toFixed(2)} ms`
      : '';
    lines.push(`${name}: ${metric.count} (${(metric.count / elapsedSeconds).toFixed(1)}/s); ${metric.bytes} B${duration}`);
  }
  lines.push('Arrival = delivered binary frame; accepted = frame stored by ScopeController.');
  lines.push('Draw times measure synchronous renderer calls, not GPU presentation.');
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
  const copy = document.createElement('button');
  copy.type = 'button';
  copy.textContent = 'Copy summary';
  copy.style.cssText = 'margin:6px 0;padding:4px 8px';
  copy.onclick = async () => {
    try {
      await navigator.clipboard.writeText(formatSummary());
      copy.textContent = 'Copied';
    } catch {
      copy.textContent = 'Copy unavailable; select text below';
    }
  };
  panel.append(copy);
  summary = document.createElement('pre');
  summary.style.cssText = 'white-space:pre-wrap;user-select:text;margin:4px 0';
  panel.append(summary);
  document.body.append(panel);
  summary.textContent = formatSummary();
  setInterval(() => { if (summary) summary.textContent = formatSummary(); }, 5000);
}

function start(): void {
  if (startedAt) return;
  startedAt = performance.now();
  if (document.body) installOverlay();
  else document.addEventListener('DOMContentLoaded', installOverlay, { once: true });
}

export function recordUiMetric(metric: Metric, bytes = 0, durationMs = 0): void {
  if (!UI_TELEMETRY_ENABLED) return;
  start();
  const entry = metrics[metric];
  entry.count++;
  entry.bytes += bytes;
  entry.durationMs += durationMs;
  entry.maxMs = Math.max(entry.maxMs, durationMs);
}

export function controlTextBytes(value: string): number {
  if (!UI_TELEMETRY_ENABLED) return 0;
  textEncoder ??= new TextEncoder();
  return textEncoder.encode(value).byteLength;
}

// This module is loaded by the UI on startup. The build flag is replaced by
// Vite, so ordinary builds never create a timer or an overlay.
if (UI_TELEMETRY_ENABLED) start();
