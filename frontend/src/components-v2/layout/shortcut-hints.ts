import * as capabilities from '$lib/stores/capabilities.svelte';

import { findBindingByAction, formatShortcut } from './keyboard-map';

export function getShortcutHint(
  action: string,
  predicate?: Parameters<typeof findBindingByAction>[2],
): string | null {
  // Namespace access: several unit tests mock `capabilities.svelte` with a
  // partial export set and no `getKeyboardConfig`. Optional call keeps those
  // mocks from throwing when a panel asks for a hint.
  const config = capabilities.getKeyboardConfig?.() ?? null;
  const binding = findBindingByAction(config, action, predicate);
  return binding ? formatShortcut(binding) : null;
}

export function joinShortcutHints(...hints: Array<string | null | undefined>): string | null {
  const values = hints.filter((hint): hint is string => Boolean(hint));
  return values.length > 0 ? values.join(' / ') : null;
}