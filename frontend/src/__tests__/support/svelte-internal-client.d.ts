declare module 'svelte/internal/client' {
  export function user_effect(fn: () => void | (() => void)): unknown;
}
