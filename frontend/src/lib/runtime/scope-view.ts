/** Source-independent display geometry. */
export interface View {
  readonly startHz: number;
  readonly endHz: number;
}

export type ScopeViewRequest = View | { readonly follow: 'vfo'; readonly spanHz: number };
export interface ScopeSubscription {
  readonly sourceId: string;
  readonly receiver: number | null;
  readonly view: ScopeViewRequest;
  readonly width: number;
  readonly maxFps: number;
}

export function viewCenterHz(view: View): number { return (view.startHz + view.endHz) / 2; }
export function viewSpanHz(view: View): number { return view.endHz - view.startHz; }
export function panView(view: View, deltaHz: number): View {
  return { startHz: view.startHz + deltaHz, endHz: view.endHz + deltaHz };
}
export function zoomView(view: View, factor: number): View {
  const halfSpan = viewSpanHz(view) * factor / 2;
  const center = viewCenterHz(view);
  return { startHz: center - halfSpan, endHz: center + halfSpan };
}

type Leg = 'confirmed' | 'pending' | 'tuningBurst' | 'gesture';
type Listener = (view: View) => void;

/** A Svelte-compatible readable store; setting a hidden leg does not notify. */
export function createScopeView(confirmed: View) {
  const legs: Record<Leg, View | null> = { confirmed, pending: null, tuningBurst: null, gesture: null };
  const listeners = new Set<Listener>();
  let current = confirmed;
  return {
    subscribe(listener: Listener): () => void {
      listeners.add(listener);
      listener(current);
      return () => listeners.delete(listener);
    },
    set(leg: Leg, value: View | null): void {
      if (leg === 'confirmed' && value === null) return;
      legs[leg] = value;
      const next = legs.gesture ?? legs.tuningBurst ?? legs.pending ?? legs.confirmed!;
      if (next.startHz === current.startHz && next.endHz === current.endHz) return;
      current = next;
      for (const listener of listeners) listener(current);
    },
  };
}

export function scopeSubscription(input: {
  sourceId: string;
  receiver: number | null;
  view: ScopeViewRequest;
  width: number;
  visible: boolean;
  maxFps: number;
}): ScopeSubscription {
  return {
    sourceId: input.sourceId,
    receiver: input.receiver,
    view: input.view,
    width: input.width,
    maxFps: input.visible && input.width > 0 ? input.maxFps : 0,
  };
}
