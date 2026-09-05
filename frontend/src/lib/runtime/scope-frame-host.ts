import { toScopeDisplayFrame } from './adapters/scope-adapter';
import type { ScopeController, ScopeFrameEvidence } from './scope-controller.svelte';
import {
  resolveLcdSpectrumFrame,
  type LcdSpectrumFrameResolution,
  type LcdSpectrumReceiver,
  type LcdSpectrumSource,
} from '../../skins/segmentline/lcd-display-contract';

export interface ScopeFrameHostAuthority {
  readonly source: LcdSpectrumSource;
  readonly receiver: LcdSpectrumReceiver | null;
  readonly providerGeneration: number | null;
}

type ResolutionHandler = (resolution: LcdSpectrumFrameResolution) => void;
export interface ScopeFramePresentation extends ScopeFrameEvidence {
  readonly resolution: LcdSpectrumFrameResolution;
}
type PresentationHandler = (presentation: ScopeFramePresentation) => void;

/**
 * Runtime-side owner of the MOR-2321 display-frame resolution. Wiring supplies
 * canonical provider/receiver/source authority; the controller supplies
 * demand, transport epoch, receipt envelope, clock, and expiry notifications.
 */
export class ScopeFrameHost {
  private readonly listeners = new Map<number, ResolutionHandler>();
  private readonly presentationListeners = new Map<number, PresentationHandler>();
  private nextId = 0;
  private authority: ScopeFrameHostAuthority | null = null;
  private unsubscribeEvidence: () => void;
  private disposed = false;

  constructor(private readonly controller: ScopeController) {
    this.unsubscribeEvidence = controller.subscribeFrameEvidence(() => this.publish());
  }

  updateAuthority(authority: ScopeFrameHostAuthority | null): void {
    if (this.disposed) return;
    const normalized = authority ? Object.freeze({ ...authority }) : null;
    if (this.authority?.source === normalized?.source
      && this.authority?.receiver === normalized?.receiver
      && this.authority?.providerGeneration === normalized?.providerGeneration) return;
    this.authority = normalized;
    this.controller.setFrameAuthority(normalized ? {
      source: normalized.source === 'audio-fft' ? 'audio_fft' : 'hardware',
      receiver: normalized.receiver === null ? null : normalized.receiver === 'MAIN' ? 0 : 1,
      providerGeneration: normalized.providerGeneration,
    } : null);
  }

  snapshot(): LcdSpectrumFrameResolution {
    return this.snapshotPresentation().resolution;
  }

  snapshotPresentation(): ScopeFramePresentation {
    const evidence = this.controller.snapshotFrameEvidence();
    const authority = this.authority;
    const candidate = authority ? toScopeDisplayFrame(evidence.envelope, evidence.authority) : null;
    const resolution = Object.freeze(resolveLcdSpectrumFrame(candidate, {
      source: authority?.source ?? 'hardware',
      receiver: authority?.receiver ?? null,
    }));
    return Object.freeze({ ...evidence, resolution });
  }

  subscribePresentation(handler: PresentationHandler): () => void {
    if (this.disposed) return () => {};
    const id = this.nextId++;
    this.presentationListeners.set(id, handler);
    return () => { this.presentationListeners.delete(id); };
  }

  subscribe(handler: ResolutionHandler): () => void {
    if (this.disposed) return () => {};
    const id = this.nextId++;
    this.listeners.set(id, handler);
    return () => { this.listeners.delete(id); };
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    this.unsubscribeEvidence();
    this.controller.setFrameAuthority(null);
    this.listeners.clear();
    this.presentationListeners.clear();
  }

  private publish(): void {
    if (this.disposed) return;
    const presentation = this.snapshotPresentation();
    for (const listener of [...this.listeners.values()]) {
      try {
        listener(presentation.resolution);
      } catch (error) {
        console.warn('Scope frame host subscriber failed', error);
      }
    }
    for (const listener of [...this.presentationListeners.values()]) {
      try {
        listener(presentation);
      } catch (error) {
        console.warn('Scope frame host subscriber failed', error);
      }
    }
  }
}
