import { getKeyboardConfig } from '$lib/stores/capabilities.svelte';

import { findBindingByAction, formatShortcut } from './keyboard-map';

export function getShortcutHint(
  action: string,
  predicate?: Parameters<typeof findBindingByAction>[2],
): string | null {
  const binding = findBindingByAction(getKeyboardConfig(), action, predicate);
  return binding ? formatShortcut(binding) : null;
}

export function joinShortcutHints(...hints: Array<string | null | undefined>): string | null {
  const values = hints.filter((hint): hint is string => Boolean(hint));
  return values.length > 0 ? values.join(' / ') : null;
}