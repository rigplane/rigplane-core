import { toScopeDisplayFrame } from './adapters/scope-adapter';
import type { ScopeController } from './scope-controller.svelte';
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

/**
 * Runtime-side owner of the MOR-2321 display-frame resolution. Wiring supplies
 * canonical provider/receiver/source authority; the controller supplies
 * demand, transport epoch, receipt envelope, clock, and expiry notifications.
 */
export class ScopeFrameHost {
  private readonly listeners = new Map<number, ResolutionHandler>();
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
    return this.resolve();
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
  }

  private resolve(): LcdSpectrumFrameResolution {
    const authority = this.authority;
    if (!authority) {
      return resolveLcdSpectrumFrame(null, { source: 'hardware', receiver: null });
    }
    const evidence = this.controller.snapshotFrameEvidence();
    const candidate = toScopeDisplayFrame(evidence.envelope, evidence.authority);
    return resolveLcdSpectrumFrame(candidate, {
      source: authority.source,
      receiver: authority.receiver,
    });
  }

  private publish(): void {
    if (this.disposed) return;
    const resolution = this.resolve();
    for (const listener of [...this.listeners.values()]) {
      try {
        listener(resolution);
      } catch (error) {
        console.warn('Scope frame host subscriber failed', error);
      }
    }
  }
}
