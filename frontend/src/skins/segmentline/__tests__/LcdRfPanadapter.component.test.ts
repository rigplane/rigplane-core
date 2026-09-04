import { readFileSync } from 'node:fs';
import { afterEach, describe, expect, it } from 'vitest';
import { mount, unmount } from 'svelte';
import LcdRfPanadapter, {
  type LcdRfPanadapterFrame,
} from '../LcdRfPanadapter.svelte';

let component: ReturnType<typeof mount> | null = null;

afterEach(() => {
  if (component) unmount(component);
  component = null;
  document.body.innerHTML = '';
});

function render(
  frame?: LcdRfPanadapterFrame,
  receiver: 'MAIN' | 'SUB' | null = 'MAIN',
): HTMLElement {
  const target = document.createElement('div');
  document.body.appendChild(target);
  component = mount(LcdRfPanadapter, { target, props: { receiver, frame } });
  return target;
}

describe('LcdRfPanadapter', () => {
  it('renders grid-only ghost geometry when no frame is supplied', () => {
    const target = render();
    const scope = target.querySelector('[data-testid="lcd-rf-panadapter"]');

    expect(scope?.getAttribute('data-rf-mode')).toBe('ghost');
    expect(scope?.getAttribute('data-frame-reason')).toBe('missing');
    expect(target.querySelectorAll('[data-rf-bin]')).toHaveLength(0);
    expect(target.querySelectorAll('.rf-grid')).not.toHaveLength(0);
    expect(target.querySelector('.rf-trace')).toBeNull();
  });

  it.each([
    ['stale', { receiver: 'MAIN', freshness: 'stale', normalizedBins: [0.1, 0.8] }],
    ['receiver-mismatch', { receiver: 'SUB', freshness: 'fresh', normalizedBins: [0.1, 0.8] }],
    ['invalid', { receiver: 'MAIN', freshness: 'fresh', normalizedBins: [0.1, Number.NaN] }],
  ] as const)('fails closed for a %s frame', (reason, frame) => {
    const target = render(frame);
    const scope = target.querySelector('[data-testid="lcd-rf-panadapter"]');

    expect(scope?.getAttribute('data-rf-mode')).toBe('ghost');
    expect(scope?.getAttribute('data-frame-reason')).toBe(reason);
    expect(target.querySelectorAll('[data-rf-bin]')).toHaveLength(0);
  });

  it('fails closed when receiver identity is unavailable', () => {
    const target = render({
      receiver: 'MAIN', freshness: 'fresh', normalizedBins: [0.1, 0.8],
    }, null);

    expect(target.querySelector('[data-testid="lcd-rf-panadapter"]')
      ?.getAttribute('data-frame-reason')).toBe('receiver-unknown');
    expect(target.querySelectorAll('[data-rf-bin]')).toHaveLength(0);
  });

  it('renders exactly the finite samples supplied by a fresh matching frame', () => {
    const bins = [0.15, 0.7, 0.35];
    const target = render({ receiver: 'MAIN', freshness: 'fresh', normalizedBins: bins });
    const renderedBins = target.querySelectorAll('[data-rf-bin]');

    expect(target.querySelector('[data-testid="lcd-rf-panadapter"]')
      ?.getAttribute('data-rf-mode')).toBe('live');
    expect(renderedBins).toHaveLength(bins.length);
    expect([...renderedBins].map((node) => Number(node.getAttribute('data-rf-sample'))))
      .toEqual(bins);
  });

  it('is passive and keeps resource ownership outside the renderer', () => {
    const target = render({
      receiver: 'MAIN', freshness: 'fresh', normalizedBins: [0.1, 0.8],
    });
    const source = readFileSync('src/skins/segmentline/LcdRfPanadapter.svelte', 'utf8');

    expect(target.querySelectorAll(
      'button,input,select,a[href],[tabindex],[role="button"],[role="switch"]',
    )).toHaveLength(0);
    expect(target.innerHTML).not.toMatch(/onclick|onpointer|onwheel/i);
    expect(source).not.toMatch(/(?:lib\/runtime|stores?\/|controller|transport|socket|demand)/i);
  });
});
