import { vi } from 'vitest';

export function stubResizeObserver(): void {
  vi.stubGlobal('ResizeObserver', class {
    observe() {}
    unobserve() {}
    disconnect() {}
  });
}
