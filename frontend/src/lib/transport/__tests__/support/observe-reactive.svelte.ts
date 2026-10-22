/**
 * Runs `read` inside an effect, so Svelte re-runs it only when a reactive
 * value it read has changed, and records each result. Calling `read`
 * directly cannot show whether anything makes it run again.
 */
export function observeReactive<T>(read: () => T): { values: T[]; stop: () => void } {
  const values: T[] = [];
  const stop = $effect.root(() => {
    $effect(() => {
      values.push(read());
    });
  });
  return { values, stop };
}
