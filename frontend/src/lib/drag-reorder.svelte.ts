/**
 * Shared drag-to-reorder logic for sidebar panels,
 * with cross-sidebar drag support.
 *
 * Usage:
 *   const drag = createDragReorder({
 *     storageKey: 'rigplane:panel-order',
 *     defaults: ['rf-front-end', 'mode', ...],
 *     containerSelector: '.left-sidebar',
 *   });
 *
 * Cross-sidebar linking happens automatically via module-level registry.
 * When two instances exist, dragging a panel over the peer sidebar
 * triggers cross-sidebar drop detection and panel transfer.
 */

// --- Pure helpers (exported for testing) ---

export const KNOWN_DEFAULTS_SUFFIX = ':known-defaults';

export function loadKnownDefaults(storageKey: string): Set<string> {
  try {
    const raw = localStorage.getItem(storageKey + KNOWN_DEFAULTS_SUFFIX);
    if (raw) {
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed)) {
        return new Set(parsed.filter((id): id is string => typeof id === 'string'));
      }
    }
  } catch {
    /* ignore */
  }
  return new Set<string>();
}

export function saveKnownDefaults(storageKey: string, ids: Iterable<string>): void {
  try {
    localStorage.setItem(storageKey + KNOWN_DEFAULTS_SUFFIX, JSON.stringify([...ids]));
  } catch {
    /* ignore */
  }
}

export function loadPanelOrder(storageKey: string, defaults: string[]): string[] {
  const known = loadKnownDefaults(storageKey);
  // Union known-defaults with the current defaults list so that future loads
  // recognise today's defaults as "already presented".
  const nextKnown = new Set<string>(known);
  for (const id of defaults) nextKnown.add(id);

  try {
    const stored = localStorage.getItem(storageKey);
    if (stored) {
      const parsed = JSON.parse(stored);
      if (Array.isArray(parsed) && parsed.length > 0) {
        // Accept variable-length orders (cross-sidebar moves change panel count).
        // Filter to strings only, deduplicate, ignore unknowns at render time.
        const seen = new Set<string>();
        const unique: string[] = [];
        for (const id of parsed) {
          if (typeof id === 'string' && !seen.has(id)) {
            seen.add(id);
            unique.push(id);
          }
        }
        if (unique.length > 0) {
          // Append ONLY defaults that the app has never presented to this
          // sidebar before (i.e. newly introduced panels). Defaults that are
          // already in `known` but absent from the stored order were
          // deliberately removed by the user (e.g. dragged to the peer
          // sidebar) — re-adding them would duplicate the panel across both
          // sidebars. Unknown (peer-owned) ids stay untouched.
          for (const id of defaults) {
            if (!seen.has(id) && !known.has(id)) {
              seen.add(id);
              unique.push(id);
            }
          }
          saveKnownDefaults(storageKey, nextKnown);
          return unique;
        }
      }
    }
  } catch {
    /* ignore */
  }
  saveKnownDefaults(storageKey, nextKnown);
  return [...defaults];
}

export function reorderPanels(order: string[], fromId: string, toIndex: number): string[] {
  const fromIndex = order.indexOf(fromId);
  if (fromIndex < 0 || fromIndex === toIndex) return order;
  const newOrder = [...order];
  const [moved] = newOrder.splice(fromIndex, 1);
  newOrder.splice(toIndex, 0, moved);
  return newOrder;
}

// --- Module-level peer registry ---

interface DragInstance {
  readonly order: string[];
  readonly isDropTarget: boolean;
  readonly containerSelector: string;
  readonly _incomingDropIndex: number;
  orderOf(panelId: string): number;
  dragStyle(panelId: string): string;
  handleDragStart(panelId: string, event: PointerEvent): void;
  reset(): void;
  resetAll(): void;
  _setIncoming(panelId: string | null, index: number): void;
  _acceptPanel(panelId: string, atIndex: number): void;
  _removePanel(panelId: string): void;
}

const _registry: DragInstance[] = [];

// --- Reactive factory ---

export interface DragReorderOptions {
  storageKey: string;
  defaults: string[];
  containerSelector: string;
}

export function createDragReorder(options: DragReorderOptions): DragInstance {
  const { storageKey, defaults, containerSelector } = options;

  let order = $state(loadPanelOrder(storageKey, defaults));
  let dragPanelId = $state<string | null>(null);
  let dropTargetIndex = $state<number>(-1);

  // Cross-sidebar state (set by peer during its drag)
  let _incomingDragId = $state<string | null>(null);
  let _incomingDropIdx = $state<number>(-1);

  // Persist order changes
  $effect(() => {
    try {
      localStorage.setItem(storageKey, JSON.stringify(order));
    } catch {
      /* ignore */
    }
  });

  function orderOf(panelId: string): number {
    return order.indexOf(panelId);
  }

  function dragStyle(panelId: string): string {
    const idx = `order:${orderOf(panelId)};`;
    if (dragPanelId === panelId) return idx + 'opacity:0.5;transform:scale(0.98);';
    if (dragPanelId && orderOf(panelId) === dropTargetIndex)
      return idx + 'border-top:2px solid var(--v2-accent, #4af);';
    if (_incomingDragId && orderOf(panelId) === _incomingDropIdx)
      return idx + 'border-top:2px solid var(--v2-accent, #4af);';
    return idx;
  }

  function _setIncoming(panelId: string | null, index: number) {
    _incomingDragId = panelId;
    _incomingDropIdx = index;
  }

  function _acceptPanel(panelId: string, atIndex: number) {
    const newOrder = [...order];
    newOrder.splice(Math.min(atIndex, newOrder.length), 0, panelId);
    order = newOrder;
  }

  function _removePanel(panelId: string) {
    order = order.filter((id) => id !== panelId);
  }

  function findDropIndex(
    targetOrder: string[],
    targetRects: Map<string, DOMRect>,
    clientY: number,
  ): number {
    let closest = 0;
    let minDist = Infinity;
    for (let i = 0; i < targetOrder.length; i++) {
      const rect = targetRects.get(targetOrder[i]);
      if (!rect) continue;
      const dist = Math.abs(clientY - (rect.top + rect.height / 2));
      if (dist < minDist) {
        minDist = dist;
        closest = i;
      }
    }
    return closest;
  }

  function handleDragStart(panelId: string, event: PointerEvent) {
    const handle = event.currentTarget as HTMLElement;
    handle.setPointerCapture(event.pointerId);
    dragPanelId = panelId;
    dropTargetIndex = order.indexOf(panelId);

    const sidebar = handle.closest(containerSelector) as HTMLElement;
    if (!sidebar) return;

    // Cache own panel rects
    const rects = new Map<string, DOMRect>();
    for (const p of sidebar.querySelectorAll<HTMLElement>('[data-panel-id]')) {
      rects.set(p.dataset.panelId!, p.getBoundingClientRect());
    }

    // Find peer from registry and cache its rects
    const peer = _registry.find((r) => r !== instance);
    let peerRect: DOMRect | null = null;
    let peerRects: Map<string, DOMRect> | null = null;
    if (peer) {
      const peerEl = document.querySelector(peer.containerSelector) as HTMLElement;
      if (peerEl) {
        peerRect = peerEl.getBoundingClientRect();
        peerRects = new Map();
        for (const p of peerEl.querySelectorAll<HTMLElement>('[data-panel-id]')) {
          peerRects.set(p.dataset.panelId!, p.getBoundingClientRect());
        }
      }
    }

    let isOverPeer = false;

    function onMove(e: PointerEvent) {
      if (
        peer &&
        peerRect &&
        peerRects &&
        e.clientX >= peerRect.left &&
        e.clientX <= peerRect.right &&
        e.clientY >= peerRect.top &&
        e.clientY <= peerRect.bottom
      ) {
        // Cursor is over peer sidebar
        if (!isOverPeer) {
          dropTargetIndex = -1;
        }
        isOverPeer = true;
        const peerOrder = peer.order;
        const idx = peerOrder.length === 0 ? 0 : findDropIndex(peerOrder, peerRects, e.clientY);
        peer._setIncoming(panelId, idx);
      } else {
        // Cursor is over own sidebar (or between)
        if (isOverPeer && peer) {
          peer._setIncoming(null, -1);
        }
        isOverPeer = false;
        dropTargetIndex = findDropIndex(order, rects, e.clientY);
      }
    }

    function onUp() {
      if (isOverPeer && peer && dragPanelId) {
        const targetIdx = peer._incomingDropIndex;
        peer._acceptPanel(dragPanelId, targetIdx >= 0 ? targetIdx : 0);
        _removePanel(dragPanelId);
        peer._setIncoming(null, -1);
      } else if (dragPanelId && dropTargetIndex >= 0) {
        const newOrder = reorderPanels(order, dragPanelId, dropTargetIndex);
        if (newOrder !== order) order = newOrder;
      }
      dragPanelId = null;
      dropTargetIndex = -1;
      handle.removeEventListener('pointermove', onMove);
      handle.removeEventListener('pointerup', onUp);
      handle.removeEventListener('pointercancel', onUp);
    }

    handle.addEventListener('pointermove', onMove);
    handle.addEventListener('pointerup', onUp);
    handle.addEventListener('pointercancel', onUp);
  }

  function reset() {
    order = [...defaults];
    try {
      localStorage.removeItem(storageKey);
      localStorage.removeItem(storageKey + KNOWN_DEFAULTS_SUFFIX);
    } catch {
      /* ignore */
    }
  }

  function resetAll() {
    reset();
    for (const other of _registry) {
      if (other !== instance) other.reset();
    }
  }

  const instance: DragInstance = {
    get order() {
      return order;
    },
    get isDropTarget() {
      return _incomingDragId !== null;
    },
    get _incomingDropIndex() {
      return _incomingDropIdx;
    },
    containerSelector,
    orderOf,
    dragStyle,
    handleDragStart,
    reset,
    resetAll,
    _setIncoming,
    _acceptPanel,
    _removePanel,
  };

  _registry.push(instance);

  // Auto-unregister when the owning component is destroyed.
  // In Svelte 5, $effect teardown runs on component unmount.
  $effect(() => {
    return () => {
      const idx = _registry.indexOf(instance);
      if (idx >= 0) _registry.splice(idx, 1);
    };
  });

  return instance;
}
