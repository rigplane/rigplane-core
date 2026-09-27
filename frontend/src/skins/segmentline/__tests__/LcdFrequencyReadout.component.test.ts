import { afterEach, describe, expect, it } from 'vitest';
import { mount, unmount } from 'svelte';
import type { DisplaySlotId, DisplayValue } from '../../../semantic/radio-display-model';
import {
  DIGIT_CELL_EM,
  DOT_CELL_EM,
} from '../../../presentation/languages/segmentline/frequency-renderer';
import LcdFrequencyReadout from '../LcdFrequencyReadout.svelte';

const known = (value: number): DisplayValue<number> => ({ state: 'known', value });
const unknown: DisplayValue<number> = { state: 'unknown' };

let component: ReturnType<typeof mount> | null = null;

afterEach(() => {
  if (component) unmount(component);
  component = null;
  document.body.innerHTML = '';
});

function render(receiver: DisplaySlotId, field: DisplayValue<number>): HTMLElement {
  const target = document.createElement('div');
  document.body.appendChild(target);
  component = mount(LcdFrequencyReadout, { target, props: { receiver, field } });
  return target;
}

const cellsOf = (target: HTMLElement) =>
  [...target.querySelectorAll('.frequency-cell')];

const textOf = (target: HTMLElement): string =>
  // Svelte may keep whitespace nodes between the cells; the pin is on the
  // literal glyph text, not on the template's line breaks.
  (target.textContent ?? '').replace(/\s+/g, '');

describe('LcdFrequencyReadout', () => {
  it('renders a known frequency exactly as today', () => {
    const target = render('MAIN', known(14_250_000));
    const readout = target.querySelector('[data-testid="lcd-frequency-MAIN"]')!;

    expect(textOf(target)).toBe('14.250.000');
    expect(readout.getAttribute('aria-label')).toBeNull();
  });

  it('renders an unread frequency as unlit digit cells with lit separators, never a dash glyph', () => {
    const target = render('MAIN', unknown);
    const readout = target.querySelector('[data-testid="lcd-frequency-MAIN"]')!;

    expect(textOf(target)).toBe('..');
    expect(textOf(target)).not.toMatch(/[?—–-]|UNKNOWN|unknown|N\/A|null|undefined|NaN/);
    expect(readout.getAttribute('aria-label')).toBe('Frequency MAIN');
    expect(readout.getAttribute('aria-label')).not.toMatch(/[?—–-]/);
  });

  it('keeps the unread digit cells on the known layout grid', () => {
    const knownCells = cellsOf(render('MAIN', known(14_250_000)));
    if (component) unmount(component);
    component = null;
    document.body.innerHTML = '';
    const unreadCells = cellsOf(render('SUB', unknown));

    expect(unreadCells).toHaveLength(knownCells.length);
    expect(unreadCells).toHaveLength(10);
    expect(unreadCells.map((cell) => cell.getAttribute('style'))).toEqual(
      knownCells.map((cell) => cell.getAttribute('style')),
    );
    expect(unreadCells.map((cell) => cell.getAttribute('style'))).toEqual([
      `width: ${DIGIT_CELL_EM}em;`,
      `width: ${DIGIT_CELL_EM}em;`,
      `width: ${DOT_CELL_EM}em;`,
      `width: ${DIGIT_CELL_EM}em;`,
      `width: ${DIGIT_CELL_EM}em;`,
      `width: ${DIGIT_CELL_EM}em;`,
      `width: ${DOT_CELL_EM}em;`,
      `width: ${DIGIT_CELL_EM}em;`,
      `width: ${DIGIT_CELL_EM}em;`,
      `width: ${DIGIT_CELL_EM}em;`,
    ]);
    // 8 unlit digits + 2 lit fixed decimal separators — no invented value.
    expect(unreadCells.filter((cell) => cell.textContent === '').length).toBe(8);
    expect(unreadCells.filter((cell) => cell.textContent === '.').length).toBe(2);
    // The unread slot keeps the known static offsets: 2 digits + dot, then
    // 3 digits + dot, then 3 digits (14.250.000 shape).
    expect(unreadCells.filter((cell) => cell.classList.contains('separator')).length).toBe(2);
  });
});
