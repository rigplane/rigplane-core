import { describe, expect, it } from 'vitest';

import {
  DEFAULT_KEYBOARD_CONFIG,
  isDigitKey,
  isFrequencyDisplayFocused,
  resolveAction,
  shouldIgnoreEvent,
} from '../keyboard-map';
import {
  resetLocalExtensionKeyboardScope,
  setLocalExtensionKeyboardScope,
} from '$lib/local-extensions/keyboard-scope';

describe('resolveAction', () => {
  it('ArrowLeft tunes down by the current frontend step', () => {
    const action = resolveAction({ key: 'ArrowLeft' }, DEFAULT_KEYBOARD_CONFIG);

    expect(action).toEqual(
      expect.objectContaining({
        action: 'tune',
        params: { direction: 'down', fine: false },
      }),
    );
  });

  it('ArrowRight tunes up by the current frontend step', () => {
    const action = resolveAction({ key: 'ArrowRight' }, DEFAULT_KEYBOARD_CONFIG);

    expect(action).toEqual(
      expect.objectContaining({
        action: 'tune',
        params: { direction: 'up', fine: false },
      }),
    );
  });

  it('ArrowUp selects the next tuning step', () => {
    const action = resolveAction({ key: 'ArrowUp' }, DEFAULT_KEYBOARD_CONFIG);

    expect(action).toEqual(
      expect.objectContaining({
        action: 'adjust_tuning_step',
        params: { direction: 'up' },
      }),
    );
  });

  it('ArrowDown selects the previous tuning step', () => {
    const action = resolveAction({ key: 'ArrowDown' }, DEFAULT_KEYBOARD_CONFIG);

    expect(action).toEqual(
      expect.objectContaining({
        action: 'adjust_tuning_step',
        params: { direction: 'down' },
      }),
    );
  });

  it('returns null for an unbound key', () => {
    expect(resolveAction({ key: 'q' }, DEFAULT_KEYBOARD_CONFIG)).toBeNull();
  });

  it.each([
    ['[', false, 'scope_span_step', { direction: 'down' }],
    [']', false, 'scope_span_step', { direction: 'up' }],
    ['-', false, 'scope_ref_step', { direction: 'down' }],
    ['+', true, 'scope_ref_step', { direction: 'up' }],
    ['H', true, 'scope_toggle_hold', undefined],
    ['D', true, 'scope_toggle_dual', undefined],
    ['F', true, 'scope_toggle_fst', undefined],
  ] as const)('binds %s to %s', (key, shiftKey, action, params) => {
    const resolved = resolveAction({ key, shiftKey }, DEFAULT_KEYBOARD_CONFIG);
    expect(resolved?.action).toBe(action);
    expect(resolved?.params).toEqual(params);
  });

  it('m toggles the active receiver', () => {
    const action = resolveAction({ key: 'm' }, DEFAULT_KEYBOARD_CONFIG);
    expect(action?.action).toBe('switch_active_vfo');
  });

  it('Shift+M activates MAIN', () => {
    const action = resolveAction({ key: 'M', shiftKey: true }, DEFAULT_KEYBOARD_CONFIG);
    expect(action?.action).toBe('set_active_vfo');
    expect(action?.params).toEqual({ vfo: 'MAIN' });
  });

  it('Shift+S activates SUB', () => {
    const action = resolveAction({ key: 'S', shiftKey: true }, DEFAULT_KEYBOARD_CONFIG);
    expect(action?.action).toBe('set_active_vfo');
    expect(action?.params).toEqual({ vfo: 'SUB' });
  });

  it('m without shift does not resolve to set_active_vfo', () => {
    const action = resolveAction({ key: 'm', shiftKey: false }, DEFAULT_KEYBOARD_CONFIG);
    expect(action?.action).not.toBe('set_active_vfo');
    expect(action?.action).toBe('switch_active_vfo');
  });
});

describe('shouldIgnoreEvent', () => {
  function makeEl(tag: string): Element {
    return { tagName: tag } as Element;
  }

  it('suppresses shortcuts on editable elements', () => {
    expect(shouldIgnoreEvent(makeEl('INPUT'))).toBe(true);
    expect(shouldIgnoreEvent(makeEl('TEXTAREA'))).toBe(true);
    expect(shouldIgnoreEvent(makeEl('SELECT'))).toBe(true);
  });

  it('keeps shortcuts active on non-editable elements', () => {
    resetLocalExtensionKeyboardScope();
    expect(shouldIgnoreEvent(makeEl('DIV'))).toBe(false);
    expect(shouldIgnoreEvent(makeEl('BUTTON'))).toBe(false);
    expect(shouldIgnoreEvent(null)).toBe(false);
  });

  it('suppresses shortcuts while a local extension owns keyboard scope', () => {
    setLocalExtensionKeyboardScope('extension-input');

    expect(shouldIgnoreEvent(makeEl('DIV'))).toBe(true);
    expect(shouldIgnoreEvent(null)).toBe(true);

    resetLocalExtensionKeyboardScope();
  });
});

// MOR-1444 — digit keys must reach BandSurface's frequency-entry input
// instead of a band hotkey when the VFO/frequency display has focus.
describe('isDigitKey', () => {
  it.each(['0', '1', '5', '9'])('treats %s as a digit', (key) => {
    expect(isDigitKey(key)).toBe(true);
  });

  it.each(['a', 'Enter', 'Tab', '10', '', '-'])('does not treat %s as a digit', (key) => {
    expect(isDigitKey(key)).toBe(false);
  });
});

describe('isFrequencyDisplayFocused', () => {
  /**
   * Mirrors VfoSurface.svelte's real DOM shape: a `[data-vfo-tile]` wrapping
   * `data-vfo-active` (the RECEIVER-wide active flag) around a `[data-vfo-freq]`
   * span. `active` defaults to true so the pre-existing tests below (written
   * before the MOR-1444 B1 fix) keep exercising "the active tile's display".
   */
  function buildVfoTile(active = true): { tile: HTMLElement; freq: HTMLElement } {
    const tile = document.createElement('div');
    tile.setAttribute('data-vfo-tile', '');
    tile.setAttribute('data-vfo-active', String(active));
    const freq = document.createElement('span');
    freq.setAttribute('data-vfo-freq', '');
    tile.appendChild(freq);
    document.body.appendChild(tile);
    return { tile, freq };
  }

  it('is true when the active element sits inside the active tile\'s [data-vfo-freq]', () => {
    const { tile, freq } = buildVfoTile(true);
    const freqRoot = document.createElement('div');
    freq.appendChild(freqRoot);

    expect(isFrequencyDisplayFocused(freqRoot)).toBe(true);

    tile.remove();
  });

  it('is true for the [data-vfo-freq] element itself on the active tile', () => {
    const { tile, freq } = buildVfoTile(true);

    expect(isFrequencyDisplayFocused(freq)).toBe(true);

    tile.remove();
  });

  // MOR-1444 B1 — reproduced: on 2/main_sub both receivers mount a focusable
  // [data-vfo-freq] (hasTunableFrequency gates on isActiveSlot, not isActive
  // — VfoSurface.svelte:258-259), but only the ACTIVE RECEIVER's tile is the
  // honest dispatch target for enterFrequency()'s view.activeReceiver write.
  // Focusing the INACTIVE receiver's display must not qualify as "the VFO
  // display is focused" for routing purposes — reopening the MOR-1322 B1 /
  // MOR-1335 G4 cross-dispatch class this predicate exists to keep shut.
  it('is false when the active element sits inside an INACTIVE tile\'s [data-vfo-freq]', () => {
    const { tile, freq } = buildVfoTile(false);
    const freqRoot = document.createElement('div');
    freq.appendChild(freqRoot);

    expect(isFrequencyDisplayFocused(freqRoot)).toBe(false);

    tile.remove();
  });

  it('is false for the [data-vfo-freq] element itself on an inactive tile', () => {
    const { tile, freq } = buildVfoTile(false);

    expect(isFrequencyDisplayFocused(freq)).toBe(false);

    tile.remove();
  });

  it('is false for an element outside any [data-vfo-freq] ancestor', () => {
    const el = document.createElement('div');
    document.body.appendChild(el);

    expect(isFrequencyDisplayFocused(el)).toBe(false);

    el.remove();
  });

  it('is false for null', () => {
    expect(isFrequencyDisplayFocused(null)).toBe(false);
  });
});
