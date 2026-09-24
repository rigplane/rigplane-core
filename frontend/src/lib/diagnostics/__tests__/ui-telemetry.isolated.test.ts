import { afterEach, expect, it, vi } from 'vitest';

afterEach(() => {
  vi.unstubAllEnvs();
  vi.useRealTimers();
  document.body.replaceChildren();
});

it('reports fixed control categories and delta shape without exposing values, then resets the window', async () => {
  vi.useFakeTimers();
  vi.stubEnv('VITE_UI_DIAGNOSTICS', '1');
  vi.resetModules();
  const telemetry = await import('../ui-telemetry');
  for (const units of [40, 127, 31, 23, 9]) telemetry.recordUiMetric('controlText', units);
  telemetry.recordControlEnvelope({ type: 'state_update', data: { type: 'full', data: {} } }, 40);
  telemetry.recordControlEnvelope({
    type: 'state_update', data: {
      type: 'delta', changed: {
        fieldStatus: { 'private.path': { value: 'private-value' }, 'other.path': {} },
        main: { secret: 'private-value' }, 'private.top.level': 'private-value',
      },
    },
  }, 127);
  telemetry.recordControlEnvelope({ type: 'response', id: 'private-id' }, 31);
  telemetry.recordControlEnvelope({ type: 'unknown-private-type', data: 'private-value' }, 23);
  telemetry.recordMalformedControl(9);

  const report = telemetry.uiTelemetrySummary();
  expect(report).toContain('stateFull: 1; 40 UTF-16 units');
  expect(report).toContain('stateDelta: 1; 127 UTF-16 units');
  expect(report).toContain('response: 1; 31 UTF-16 units');
  expect(report).toContain('other: 1; 23 UTF-16 units');
  expect(report).toContain('malformed: 1; 9 UTF-16 units');
  expect(report).toContain('categorized total: 5; 230 UTF-16 units');
  expect(report).toContain('controlText: 5 (');
  expect(report).toContain('230 UTF-16 code units');
  expect(report).toContain('Delta changed top-level keys: 3; unlisted keys: 1');
  expect(report).toContain('Delta fieldStatus entries: 2');
  expect(report).toContain('fieldStatus=1, main=1');
  expect(report).not.toMatch(/private|secret|other\.path/);

  const reset = [...document.querySelectorAll('button')].find((button) => button.textContent === 'Reset window');
  expect(reset).toBeDefined();
  reset!.click();
  const afterReset = telemetry.uiTelemetrySummary();
  expect(afterReset).toContain('stateDelta: 0; 0 UTF-16 units');
  expect(afterReset).toContain('Delta changed top-level keys: 0; unlisted keys: 0');
  expect(afterReset).toContain('Delta fieldStatus entries: 0');
});

it('does no recording or overlay work with diagnostics disabled', async () => {
  vi.stubEnv('VITE_UI_DIAGNOSTICS', '0');
  vi.resetModules();
  const telemetry = await import('../ui-telemetry');
  telemetry.recordUiMetric('controlText', 100);
  telemetry.recordControlEnvelope({ type: 'state_update', data: { type: 'delta', changed: {} } }, 100);
  telemetry.recordMalformedControl(10);
  expect(telemetry.uiTelemetrySummary()).toBe('');
  expect(document.querySelector('details[aria-label="UI diagnostics"]')).toBeNull();
});
