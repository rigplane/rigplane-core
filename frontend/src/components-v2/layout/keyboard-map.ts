import type {
  KeyboardBindingConfig as CapKeyboardBindingConfig,
  KeyboardConfig as CapKeyboardConfig,
} from '$lib/types/capabilities';
import { isLocalExtensionKeyboardScopeActive } from '$lib/local-extensions/keyboard-scope';

export type KeyboardBindingConfig = CapKeyboardBindingConfig;
export type KeyboardConfig = CapKeyboardConfig;

export interface KeyboardActionConfig {
  id: string;
  action: string;
  params?: Record<string, unknown>;
  section: string;
  label?: string;
  description?: string;
  sequence: string[];
  modifiers?: string[];
  repeatable?: boolean;
}

export const DEFAULT_KEYBOARD_CONFIG: KeyboardConfig = {
  leaderKey: 'g',
  leaderTimeoutMs: 1000,
  altHints: true,
  helpTitle: 'Keyboard Shortcuts',
  bindings: [
    {
      id: 'step-up',
      section: 'Tuning',
      label: 'Increase tuning step',
      sequence: ['ArrowUp'],
      action: 'adjust_tuning_step',
      params: { direction: 'up' },
    },
    {
      id: 'step-down',
      section: 'Tuning',
      label: 'Decrease tuning step',
      sequence: ['ArrowDown'],
      action: 'adjust_tuning_step',
      params: { direction: 'down' },
    },
    {
      id: 'tune-down',
      section: 'Tuning',
      label: 'Tune down',
      sequence: ['ArrowLeft'],
      action: 'tune',
      repeatable: true,
      params: { direction: 'down', fine: false },
    },
    {
      id: 'tune-up',
      section: 'Tuning',
      label: 'Tune up',
      sequence: ['ArrowRight'],
      action: 'tune',
      repeatable: true,
      params: { direction: 'up', fine: false },
    },
    {
      id: 'help',
      section: 'System',
      label: 'Keyboard help',
      sequence: ['?'],
      action: 'toggle_help',
    },
    {
      id: 'switch-active-vfo',
      section: 'VFO',
      label: 'Toggle active receiver',
      sequence: ['m'],
      action: 'switch_active_vfo',
    },
    {
      id: 'activate-main',
      section: 'VFO',
      label: 'Activate MAIN receiver',
      sequence: ['M'],
      modifiers: ['SHIFT'],
      action: 'set_active_vfo',
      params: { vfo: 'MAIN' },
    },
    {
      id: 'activate-sub',
      section: 'VFO',
      label: 'Activate SUB receiver',
      sequence: ['S'],
      modifiers: ['SHIFT'],
      action: 'set_active_vfo',
      params: { vfo: 'SUB' },
    },
    {
      id: 'scope-span-down',
      section: 'Spectrum',
      label: 'Decrease scope span',
      sequence: ['['],
      action: 'scope_span_step',
      params: { direction: 'down' },
    },
    {
      id: 'scope-span-up',
      section: 'Spectrum',
      label: 'Increase scope span',
      sequence: [']'],
      action: 'scope_span_step',
      params: { direction: 'up' },
    },
    {
      id: 'scope-ref-down',
      section: 'Spectrum',
      label: 'Decrease scope reference level',
      sequence: ['-'],
      action: 'scope_ref_step',
      params: { direction: 'down' },
    },
    {
      id: 'scope-ref-up',
      section: 'Spectrum',
      label: 'Increase scope reference level',
      sequence: ['+'],
      action: 'scope_ref_step',
      params: { direction: 'up' },
    },
    {
      id: 'scope-toggle-hold',
      section: 'Spectrum',
      label: 'Toggle scope hold',
      sequence: ['H'],
      modifiers: ['SHIFT'],
      action: 'scope_toggle_hold',
    },
    {
      id: 'scope-toggle-dual',
      section: 'Spectrum',
      label: 'Toggle dual scope',
      sequence: ['D'],
      modifiers: ['SHIFT'],
      action: 'scope_toggle_dual',
    },
    {
      id: 'scope-toggle-fst',
      section: 'Spectrum',
      label: 'Toggle fast scroll (FST)',
      sequence: ['F'],
      modifiers: ['SHIFT'],
      action: 'scope_toggle_fst',
    },
  ],
};

/** Tags whose focused presence should suppress keyboard shortcuts. */
export const IGNORED_TAGS = new Set(['INPUT', 'TEXTAREA', 'SELECT']);

const MODIFIER_ORDER = ['CTRL', 'SHIFT', 'ALT', 'META'] as const;

function normalizeModifier(modifier: string): string {
  return modifier.trim().toUpperCase();
}

export function normalizeKeyboardConfig(config: KeyboardConfig | null | undefined): KeyboardConfig {
  const source = config ?? DEFAULT_KEYBOARD_CONFIG;
  return {
    leaderKey: source.leaderKey || DEFAULT_KEYBOARD_CONFIG.leaderKey,
    leaderTimeoutMs: source.leaderTimeoutMs || DEFAULT_KEYBOARD_CONFIG.leaderTimeoutMs,
    altHints: source.altHints ?? DEFAULT_KEYBOARD_CONFIG.altHints,
    helpTitle: source.helpTitle || DEFAULT_KEYBOARD_CONFIG.helpTitle,
    bindings: (source.bindings?.length ? source.bindings : DEFAULT_KEYBOARD_CONFIG.bindings).map((binding) => ({
      id: binding.id,
      action: binding.action,
      sequence: [...binding.sequence],
      section: binding.section || 'General',
      label: binding.label,
      description: binding.description,
      modifiers: binding.modifiers?.map(normalizeModifier),
      repeatable: binding.repeatable ?? false,
      params: binding.params,
    })),
  };
}

export function getEventModifiers(event: {
  ctrlKey?: boolean;
  shiftKey?: boolean;
  altKey?: boolean;
  metaKey?: boolean;
}): string[] {
  const modifiers: string[] = [];
  if (event.ctrlKey) modifiers.push('CTRL');
  if (event.shiftKey) modifiers.push('SHIFT');
  if (event.altKey) modifiers.push('ALT');
  if (event.metaKey) modifiers.push('META');
  return modifiers;
}

/**
 * Characters that are produced via Shift on a standard keyboard.
 * When the binding key is one of these and doesn't explicitly list SHIFT
 * in its modifiers, we ignore the implicit shiftKey from the event so
 * that e.g. `key = "?"` matches without `modifiers = ["SHIFT"]`.
 */
const SHIFT_PRODUCED_CHARS = new Set('~!@#$%^&*()_+{}|:"<>?');

function modifiersMatch(binding: KeyboardBindingConfig, event: { ctrlKey?: boolean; shiftKey?: boolean; altKey?: boolean; metaKey?: boolean }): boolean {
  const expected = [...(binding.modifiers ?? [])].map(normalizeModifier).sort();
  const actual = getEventModifiers(event).sort();

  // If the binding key is a Shift-produced character and the binding doesn't
  // explicitly require SHIFT, strip implicit SHIFT from actual modifiers.
  const bindingKey = binding.sequence?.[binding.sequence.length - 1] ?? '';
  const implicitShift = SHIFT_PRODUCED_CHARS.has(bindingKey)
    && !expected.includes('SHIFT');
  const filtered = implicitShift ? actual.filter((m) => m !== 'SHIFT') : actual;

  if (expected.length !== filtered.length) {
    return false;
  }
  return expected.every((modifier, index) => modifier === filtered[index]);
}

export function resolveAction(
  event: { key: string; ctrlKey?: boolean; shiftKey?: boolean; altKey?: boolean; metaKey?: boolean },
  config: KeyboardConfig | null | undefined = DEFAULT_KEYBOARD_CONFIG,
): KeyboardActionConfig | null {
  const normalized = normalizeKeyboardConfig(config);
  const binding = normalized.bindings.find(
    (candidate) => candidate.sequence.length === 1 && candidate.sequence[0] === event.key && modifiersMatch(candidate, event),
  );
  if (!binding) {
    return null;
  }
  return {
    id: binding.id,
    action: binding.action,
    params: binding.params,
    section: binding.section,
    label: binding.label,
    description: binding.description,
    sequence: binding.sequence,
    modifiers: binding.modifiers,
    repeatable: binding.repeatable,
  };
}

export function resolveSequenceStart(
  event: { key: string; ctrlKey?: boolean; shiftKey?: boolean; altKey?: boolean; metaKey?: boolean },
  config: KeyboardConfig | null | undefined = DEFAULT_KEYBOARD_CONFIG,
): KeyboardBindingConfig | null {
  const normalized = normalizeKeyboardConfig(config);
  return normalized.bindings.find(
    (binding) => binding.sequence.length > 1 && binding.sequence[0] === event.key && modifiersMatch(binding, event),
  ) ?? null;
}

export function resolveSequenceContinuation(
  binding: KeyboardBindingConfig,
  event: { key: string },
): KeyboardActionConfig | null {
  if (binding.sequence.length < 2 || binding.sequence[1] !== event.key) {
    return null;
  }
  return {
    id: binding.id,
    action: binding.action,
    params: binding.params,
    section: binding.section,
    label: binding.label,
    description: binding.description,
    sequence: binding.sequence,
    modifiers: binding.modifiers,
    repeatable: binding.repeatable,
  };
}

export function formatShortcut(binding: KeyboardBindingConfig): string {
  const sequence = binding.sequence.map((step, index) => {
    const prefix = index === 0 && binding.modifiers?.length
      ? [...binding.modifiers].map(normalizeModifier).sort((left, right) => {
          return MODIFIER_ORDER.indexOf(left as (typeof MODIFIER_ORDER)[number]) - MODIFIER_ORDER.indexOf(right as (typeof MODIFIER_ORDER)[number]);
        }).join('+') + '+'
      : '';
    return `${prefix}${step}`;
  });
  return sequence.join(' then ');
}

export function findBindingByAction(
  config: KeyboardConfig | null | undefined,
  action: string,
  predicate?: (binding: KeyboardBindingConfig) => boolean,
): KeyboardBindingConfig | null {
  const normalized = normalizeKeyboardConfig(config);
  return normalized.bindings.find((binding) => binding.action === action && (predicate ? predicate(binding) : true)) ?? null;
}

/**
 * Returns true when the active element is an editable field and
 * keyboard shortcuts should be suppressed.
 */
export function shouldIgnoreEvent(activeElement: Element | null): boolean {
  if (isLocalExtensionKeyboardScopeActive()) return true;
  if (!activeElement) return false;
  return IGNORED_TAGS.has(activeElement.tagName);
}

/** MOR-1444: true for a single digit character ("0".."9"), the key class a
 *  rig profile's keyboard config binds to `band_select`. */
export function isDigitKey(key: string): boolean {
  return key.length === 1 && key >= '0' && key <= '9';
}

/**
 * MOR-1444: true when the active element is the ACTIVE RECEIVER's
 * VFO/frequency display, or sits inside it. `data-vfo-freq` is the existing
 * production hook on the wrapper `<span>` around `FrequencyDisplayInteractive`
 * in `VfoSurface.svelte` — present for every VFO tile with a tunable
 * frequency, no new markup needed.
 *
 * MOR-1444 B1 (round-2 review, reproduced): `hasTunableFrequency` gates on
 * `isActiveSlot`, not the radio-wide `isActive` (VfoSurface.svelte:258-259)
 * — so on a dual-receiver cockpit BOTH receivers can mount a focusable
 * `[data-vfo-freq]` at once. `enterFrequency()` always writes
 * `view.activeReceiver` (SemanticRadioSurfaces.svelte), so qualifying on
 * `[data-vfo-freq]` alone would let a digit typed on the INACTIVE receiver's
 * display commit to the WRONG VFO — reopening the MOR-1322 B1 / MOR-1335 G4
 * cross-dispatch class. The ancestor `[data-vfo-tile]`'s own
 * `data-vfo-active` flag (VfoSurface.svelte:367) is the same fact
 * `hasTunableFrequency`'s sibling `tuneFrequency` guard reads, so this stays
 * a single source of truth rather than a second derivation.
 */
export function isFrequencyDisplayFocused(activeElement: Element | null): boolean {
  const freq = activeElement?.closest('[data-vfo-freq]');
  if (!freq) return false;
  return freq.closest('[data-vfo-tile]')?.getAttribute('data-vfo-active') !== 'false';
}
