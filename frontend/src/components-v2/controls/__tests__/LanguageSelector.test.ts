/**
 * Tests for the Core language preference UX (RP-ML-004, issue #1522).
 *
 * The selector is a thin shell over `$lib/i18n`:
 *   - it reads `SUPPORTED_LOCALES` to render options,
 *   - it reads `getLocale()` for the active selection,
 *   - it writes `setLocale()` on change.
 *
 * These tests verify the component honors that contract without
 * re-implementing browser-locale detection, persistence, or unsupported-
 * locale fallback (the runtime owns those — covered by `store.test.ts`).
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync, tick } from 'svelte';

import LanguageSelector from '../LanguageSelector.svelte';
import {
  getLocale,
  setLocale,
  SUPPORTED_LOCALES,
  STORAGE_KEY,
} from '$lib/i18n';
import { _resetLocale } from '$lib/i18n/store.svelte';
import enUS from '$lib/i18n/locales/en-US.json' with { type: 'json' };

function setup() {
  const target = document.createElement('div');
  document.body.appendChild(target);
  const component = mount(LanguageSelector, { target, props: {} });
  flushSync();
  return { target, component };
}

beforeEach(() => {
  localStorage.clear();
  _resetLocale();
});

afterEach(() => {
  document.body.innerHTML = '';
  localStorage.clear();
});

describe('LanguageSelector', () => {
  it('renders one option per SUPPORTED_LOCALES entry', () => {
    const { target, component } = setup();
    const options = target.querySelectorAll('option');
    // Component must not hardcode a fixed locale set — list comes from
    // SUPPORTED_LOCALES so community translators get free coverage.
    expect(options.length).toBe(SUPPORTED_LOCALES.length);
    const values = Array.from(options).map((o) => (o as HTMLOptionElement).value);
    for (const code of SUPPORTED_LOCALES) {
      expect(values).toContain(code);
    }
    unmount(component);
  });

  it('uses endonyms for known locales', () => {
    const { target, component } = setup();
    const enOpt = target.querySelector(
      '[data-testid="language-option-en-US"]',
    ) as HTMLOptionElement;
    const jaOpt = target.querySelector(
      '[data-testid="language-option-ja-JP"]',
    ) as HTMLOptionElement;
    expect(enOpt?.textContent?.trim()).toBe('English');
    expect(jaOpt?.textContent?.trim()).toBe('日本語');
    unmount(component);
  });

  it('marks the pseudo-locale as a developer pseudo-locale', () => {
    const { target, component } = setup();
    const pseudoOpt = target.querySelector(
      '[data-testid="language-option-qps-ploc"]',
    ) as HTMLOptionElement;
    expect(pseudoOpt).not.toBeNull();
    // The endonym/code stays verbatim; the en-US suffix string is appended.
    expect(pseudoOpt.textContent).toContain('qps-ploc');
    expect(pseudoOpt.textContent).toContain('developer pseudo-locale');
    unmount(component);
  });

  it('reflects the active locale in the <select> value', () => {
    setLocale('ja-JP');
    const { target, component } = setup();
    const sel = target.querySelector(
      '[data-testid="language-select"]',
    ) as HTMLSelectElement;
    expect(sel.value).toBe('ja-JP');
    unmount(component);
  });

  it('persists the explicit selection through the store on change', async () => {
    const { target, component } = setup();
    const sel = target.querySelector(
      '[data-testid="language-select"]',
    ) as HTMLSelectElement;

    sel.value = 'ja-JP';
    sel.dispatchEvent(new Event('change', { bubbles: true }));
    flushSync();
    await tick();

    expect(getLocale()).toBe('ja-JP');
    expect(localStorage.getItem(STORAGE_KEY)).toBe('ja-JP');

    unmount(component);
  });

  it('exposes a localized aria-label and title on the select', () => {
    const { target, component } = setup();
    const sel = target.querySelector(
      '[data-testid="language-select"]',
    ) as HTMLSelectElement;
    // en-US active by default — string comes from the catalog so we
    // verify it tracks the bundled English source.
    expect(sel.getAttribute('aria-label')).toBe('Choose language');
    expect(sel.getAttribute('title')).toBe('Choose language');
    unmount(component);
  });

  it('renders a hint paragraph for screen readers / sighted users', () => {
    const { target, component } = setup();
    const hint = target.querySelector('[data-testid="language-hint"]');
    expect(hint).not.toBeNull();
    expect(hint!.textContent).toContain('Selecting a language');
    unmount(component);
  });

  it('catalog source ships every key the component reads', () => {
    // Guard: if a future PR removes a key, this test will catch it
    // before the missing-key handler fires at runtime.
    const required = [
      'core.settings.language.label',
      'core.settings.language.choose',
      'core.settings.language.srHint',
      'core.settings.language.devLocaleSuffix',
    ];
    const catalog = enUS as Record<string, unknown>;
    for (const key of required) {
      expect(catalog[key], `en-US missing ${key}`).toBeTypeOf('string');
    }
  });

  it('updates the visible label when the locale flips', async () => {
    // Mount in en-US, switch to ja-JP via the store, and confirm the label
    // re-renders. This exercises the $derived(t(...)) subscription path.
    const { target, component } = setup();

    const label = () => target.querySelector('.lang-label')?.textContent?.trim();
    expect(label()).toBe('Language');

    setLocale('ja-JP');
    flushSync();
    await tick();

    expect(label()).toBe('言語');

    unmount(component);
  });
});
