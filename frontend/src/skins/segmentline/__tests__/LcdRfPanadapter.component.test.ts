import { readFileSync } from 'node:fs';
import { afterEach, describe, expect, it, beforeEach, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { lcdStreamFixture } from './scope-stream-fixture';
import { spectrumDisplayAmplitude } from '../../../lib/renderers/spectrum-renderer';
import type { LcdSpectrumFrame } from './scope-stream-fixture';
import LcdRfPanadapter, {
  type LcdRfPanadapterPassband,
} from '../LcdRfPanadapter.svelte';

let component: ReturnType<typeof mount> | null = null;
let renderedStream: ReturnType<typeof lcdStreamFixture> = null;

afterEach(() => {
  if (component) unmount(component);
  component = null;
  document.body.innerHTML = '';
});

function hardwareFrame(overrides: Partial<LcdSpectrumFrame> = {}): LcdSpectrumFrame {
  return {
    source: 'hardware',
    receiver: 'MAIN',
    freshness: 'fresh',
    startHz: 14_200_000,
    endHz: 14_300_000,
    pixels: Uint8Array.from([0.15, 0.7, 0.35], value => Math.round(value * 255)),
    ...overrides,
  };
}

function render(
  frame?: unknown,
  receiver: 'MAIN' | 'SUB' | null = 'MAIN',
  carrierHz?: number,
  passband?: LcdRfPanadapterPassband,
): HTMLElement {
  const target = document.createElement('div');
  document.body.appendChild(target);
  renderedStream = lcdStreamFixture(frame);
  component = mount(LcdRfPanadapter, {
    target, props: { receiver, scopeStream: renderedStream, carrierHz, passband },
  });
  flushSync();
  return target;
}

function numberAttribute(target: Element, selector: string, attribute: string): number {
  const value = target.querySelector(selector)?.getAttribute(attribute);
  if (value === null || value === undefined) throw new Error(`${selector} lacks ${attribute}`);
  return Number(value);
}

describe('LcdRfPanadapter', () => {
  it('renders frame-only ghost geometry when no hardware frame is supplied', () => {
    const target = render();
    const scope = target.querySelector('[data-testid="lcd-rf-panadapter"]');

    expect(scope?.getAttribute('data-rf-mode')).toBe('ghost');
    expect(scope?.getAttribute('data-frame-reason')).toBe('missing');
    expect(target.querySelectorAll(
      '[data-rf-bin],.rf-axis,.rf-grid,.rf-carrier,.rf-passband',
    )).toHaveLength(0);
    expect(target.querySelector('.rf-frame')).not.toBeNull();
  });

  it.each([
    ['stale', hardwareFrame({ freshness: 'stale' })],
    ['source-mismatch', hardwareFrame({ source: 'audio-fft' })],
    ['receiver-mismatch', hardwareFrame({ receiver: 'SUB' })],
    ['invalid', hardwareFrame({ endHz: 14_200_000 })],
    ['invalid', hardwareFrame({ pixels: new Uint8Array(1) })],
    ['invalid', hardwareFrame({ pixels: null })],
    ['invalid', { state: 'unsupported' }],
  ] as const)('fails closed for a %s frame', (reason, frame) => {
    const target = render(frame, 'MAIN', 14_250_000, {
      mode: 'USB', widthHz: 2_400, shiftHz: 0,
    });
    const scope = target.querySelector('[data-testid="lcd-rf-panadapter"]');

    expect(scope?.getAttribute('data-rf-mode')).toBe('ghost');
    expect(scope?.getAttribute('data-frame-reason')).toBe(reason === 'invalid' && !(frame && 'pixels' in frame && frame.pixels instanceof Uint8Array && frame.pixels.length === 1) ? 'missing' : reason);
    expect(target.querySelectorAll(
      '[data-rf-bin],.rf-axis,.rf-grid,.rf-carrier,.rf-passband',
    )).toHaveLength(0);
  });

  it('fails closed when receiver identity is unavailable', () => {
    const target = render(hardwareFrame(), null, 14_250_000);

    expect(target.querySelector('[data-testid="lcd-rf-panadapter"]')
      ?.getAttribute('data-frame-reason')).toBe('receiver-unknown');
    expect(target.querySelectorAll('[data-rf-bin],.rf-axis,.rf-carrier')).toHaveLength(0);
  });

  it('takes RF pixels and the absolute frequency axis only from the live frame', () => {
    const pixels = Uint8Array.from([38, 179, 89, 230]);
    const target = render(hardwareFrame({
      startHz: 7_000_000,
      endHz: 7_200_000,
      pixels,
    }));
    const scope = target.querySelector('[data-testid="lcd-rf-panadapter"]');

    expect(scope?.getAttribute('data-rf-mode')).toBe('live');
    expect(scope?.getAttribute('data-start-hz')).toBe('7000000');
    expect(scope?.getAttribute('data-end-hz')).toBe('7200000');
    expect([...target.querySelectorAll('[data-axis-frequency]')]
      .map((node) => Number(node.getAttribute('data-axis-frequency'))))
      .toEqual([7_000_000, 7_050_000, 7_100_000, 7_150_000, 7_200_000]);
    expect([...target.querySelectorAll('[data-rf-bin]')]
      .map((node) => Number(node.getAttribute('data-rf-sample')))).toEqual([...pixels]);
  });

  it.each([
    ['fixed/off-centre', hardwareFrame(), 14_240_000, 240],
    ['scrolling/centred', hardwareFrame({ startHz: 14_245_000, endHz: 14_255_000 }), 14_250_000, 300],
  ] as const)('places a real carrier for a %s frame', (_label, frame, frequencyHz, expectedX) => {
    const target = render(frame, 'MAIN', frequencyHz);

    expect(numberAttribute(target, '.rf-carrier', 'x1')).toBeCloseTo(expectedX, 6);
    expect(numberAttribute(target, '.rf-carrier', 'data-carrier-hz')).toBe(frequencyHz);
  });

  it('does not clamp an out-of-frame carrier into a false edge marker', () => {
    const target = render(hardwareFrame(), 'MAIN', 14_350_000, {
      mode: 'USB', widthHz: 2_400, shiftHz: 0,
    });

    expect(target.querySelector('.rf-carrier')).toBeNull();
    expect(target.querySelector('.rf-passband')).toBeNull();
  });

  it.each([
    ['USB', 300, 14.4],
    ['LSB', 285.6, 14.4],
    ['AM', 292.8, 14.4],
    ['FM', 292.8, 14.4],
  ] as const)('uses canonical %s passband geometry', (mode, expectedX, expectedWidth) => {
    const target = render(hardwareFrame(), 'MAIN', 14_250_000, {
      mode, widthHz: 2_400, shiftHz: 0,
    });

    expect(numberAttribute(target, '.rf-passband', 'x')).toBeCloseTo(expectedX, 6);
    expect(numberAttribute(target, '.rf-passband', 'width')).toBeCloseTo(expectedWidth, 6);
  });

  it('applies a known passband shift on the live frame scale', () => {
    const target = render(hardwareFrame(), 'MAIN', 14_250_000, {
      mode: 'USB', widthHz: 2_400, shiftHz: 1_000,
    });

    expect(numberAttribute(target, '.rf-passband', 'x')).toBeCloseTo(306, 6);
    expect(numberAttribute(target, '.rf-passband', 'width')).toBeCloseTo(14.4, 6);
  });

  it.each([
    undefined,
    { mode: 'USB', widthHz: Number.NaN, shiftHz: 0 },
    { mode: 'USB', widthHz: 2_400, shiftHz: Number.NaN },
  ])('shows no passband without completely known finite geometry', (passband) => {
    const target = render(hardwareFrame(), 'MAIN', 14_250_000, passband);

    expect(target.querySelector('.rf-carrier')).not.toBeNull();
    expect(target.querySelector('.rf-passband')).toBeNull();
  });

  it('is passive and keeps resource ownership outside the renderer', () => {
    const target = render(hardwareFrame(), 'MAIN', 14_250_000);
    const source = readFileSync('src/skins/segmentline/LcdRfPanadapter.svelte', 'utf8');

    expect(target.querySelectorAll(
      'button,input,select,a[href],[tabindex],[role="button"],[role="switch"]',
    )).toHaveLength(0);
    expect(target.innerHTML).not.toMatch(/onclick|onpointer|onwheel/i);
    expect(source.replace(/^\s*import type .*;$/gm, '').replace(/^\s*import \{ scopeSubscriptionSurface \} from '\$lib\/runtime\/scope-view';$/gm, '')).not.toMatch(/(?:lib\/runtime|stores?\/|controller|transport|socket|demand)/i);
  });
});

describe('LcdRfPanadapter hardware amplitude parity', () => {
  it('keeps raw pixels immutable while applying the shared display transfer', () => {
    const pixels = new Uint8Array([0, 40, 80, 255]);
    const target = render(hardwareFrame({ pixels }));
    const rendered = [...target.querySelectorAll('[data-rf-bin]')];

    expect([...pixels]).toEqual([0, 40, 80, 255]);
    expect(renderedStream?.latest('scope:0')?.pixels).toEqual(new Uint8Array([0, 40, 80, 255]));
    expect(rendered.map((node) => Number(node.getAttribute('data-rf-sample')))).toEqual([...pixels]);
    expect(rendered.map((node) => Number(node.getAttribute('height')))).toEqual([
      0,
      spectrumDisplayAmplitude(40, 0) * 94,
      94,
      94,
    ]);
  });

  it('continues to reject audio FFT frames instead of applying RF transfer to them', () => {
    const target = render(hardwareFrame({ source: 'audio-fft' }));

    expect(target.querySelector('[data-testid="lcd-rf-panadapter"]')
      ?.getAttribute('data-frame-reason')).toBe('source-mismatch');
    expect(target.querySelectorAll('[data-rf-bin]')).toHaveLength(0);
  });
});


beforeEach(() => { vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} }); });
afterEach(() => { vi.unstubAllGlobals(); });
