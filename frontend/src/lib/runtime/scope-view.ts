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

export interface ScopeGranted {
  readonly type: 'granted';
  readonly sourceId: string;
  readonly receiver: number | null;
  readonly view: View;
  readonly coverage: View | null;
  readonly width: number;
  readonly fps: number;
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
    /** The confirmed leg — the base a gesture pans from. */
    get confirmed(): View { return legs.confirmed!; },
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

/** Publishes CanvasSurface facts through the shared v2 subscription owner. */
export function scopeSubscriptionSurface(_node: Element, initial: {
  stream: import('./scope-stream').ScopeStream | null;
  receiver: number | null;
  view: ScopeViewRequest;
  facts: import('../canvas/canvas-surface.svelte').CanvasSurfaceFacts | null;
  maxFps?: number;
  revision?: string;
}) {
  let parameters = initial;
  const token = Symbol('scope surface');
  let disposed = false;
  let attempted = false;
  let attemptedRevision: string | undefined;
  let stopError = () => {};
  const publish = () => {
    if (disposed || parameters.stream === null) return;
    const facts = parameters.facts;
    if ((!facts || facts.backingW <= 0) && !attempted) return;
    const width = Math.max(1, Math.min(65535, facts?.backingW ?? 0));
    const sent = parameters.stream.updateSurfaceSubscription(token, scopeSubscription({
      sourceId: parameters.stream.sourceId, receiver: parameters.stream.kind === 'radio' ? parameters.receiver : null,
      view: parameters.view, width, visible: facts?.visible === true && facts.backingW > 0,
      maxFps: parameters.maxFps ?? 20,
    }), !attempted || attemptedRevision !== parameters.revision, parameters.revision);
    if (sent) { attempted = true; attemptedRevision = parameters.revision; }
  };
  const watchError = () => parameters.stream?.error.subscribe(error => {
    if (error !== null && attempted && attemptedRevision !== parameters.revision) publish();
  }) ?? (() => {});
  publish();
  stopError = watchError();
  return {
    update(next: typeof initial) {
      const changed = next.stream !== parameters.stream;
      if (changed) { stopError(); parameters.stream?.removeSurfaceSubscription(token); attempted = false; }
      parameters = next; publish();
      if (changed) stopError = watchError();
    },
    destroy() {
      disposed = true; stopError();
      parameters.stream?.removeSurfaceSubscription(token);
    },
  };
}
