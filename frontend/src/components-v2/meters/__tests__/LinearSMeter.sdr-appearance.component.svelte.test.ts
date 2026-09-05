import { afterEach, describe, expect, it } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { setCapabilities, clearCapabilities } from '$lib/stores/capabilities.svelte';
import { mockCapabilities } from '../../../../tests/e2e/i18n/fixtures';
import LinearSMeter from '../LinearSMeter.svelte';

let component: ReturnType<typeof mount> | undefined;
let target: HTMLDivElement;
function render(value: number, variant?: string) {
  target = document.createElement('div');
  document.body.appendChild(target);
  component = mount(LinearSMeter, { target, props: { value, compact: true, variant } });
  flushSync();
  return target;
}
afterEach(() => {
  if (component) unmount(component);
  target?.remove();
  clearCapabilities();
});

const calibration = [
  { raw: 0, actual: -54, label: 'S0' },
  { raw: 40, actual: -48, label: 'S1' },
  { raw: 130, actual: 0, label: 'S9' },
  { raw: 241, actual: 60, label: 'S9+60' },
];
describe('MOR-2342 opt-in SDR meter', () => {
  it('ports the dense 420x50 face while preserving calibrated S9 at zero', () => {
    setCapabilities({ ...mockCapabilities, meterCalibrations: { s_meter: calibration } });
    const root = render(0, 'sdr-screen');
    expect(root.querySelector('svg')?.getAttribute('viewBox')).toBe('0 0 420 50');
    expect(root.querySelectorAll('[data-sdr-segment]')).toHaveLength(80);
    expect(root.querySelector('svg')?.getAttribute('aria-label')).toContain('S9');
    expect(root.querySelector('svg')?.getAttribute('aria-label')).toContain('73 dBm');
    expect(root.textContent).toContain('+60');
  });
  it('does not borrow an S-unit scale for an uncalibrated radio', () => {
    clearCapabilities();
    const root = render(27, 'sdr-screen');
    expect(root.textContent).toContain('uncalibrated');
    expect(root.textContent).not.toContain('S9');
    expect(root.querySelector('svg')?.getAttribute('aria-label')).not.toContain('dBm');
  });
  it('retains the existing Standard meter and its distinct 20 segments', () => {
    const root = render(0, 'vfo-wide');
    expect(root.querySelector('svg')?.getAttribute('viewBox')).toMatch(/^0 0 600 /);
    expect(root.querySelectorAll('[data-segment]')).toHaveLength(20);
    expect(root.querySelector('[data-sdr-segment]')).toBeNull();
  });
});
