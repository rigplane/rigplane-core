/**
 * ScopeController — singleton owner of the audio-scope WebSocket channel.
 *
 * Opens `/api/v1/audio-scope` lazily when the first subscriber attaches and
 * closes it when the last subscriber detaches. Parses binary frames with
 * `parseScopeFrame()` from `scope-adapter.ts` and stores the latest frame as
 * a reactive `$state` property that Svelte components can read via `$derived`.
 *
 * Satisfies ADR INV-2 (single scope ownership) and INV-5 (mount/unmount of
 * presentation panels must not change transport state).
 *
 * @see docs/plans/2026-04-12-target-frontend-architecture.md §ScopeController
 */

import { getChannel } from '$lib/transport/ws-client';
import { markScopeFrame } from '$lib/stores/connection.svelte';
import { parseScopeFrame } from '$lib/runtime/adapters/scope-adapter';
import type { ScopeFrame } from '$lib/runtime/adapters/scope-adapter';
import type { WsChannel } from '$lib/transport/ws-client';
import type { PresentationResourceDriver, PresentationResourceHost } from './resource-host';

export type { ScopeFrame };

type FrameHandler = (frame: ScopeFrame) => void;
type ChannelFactory = (name: string) => WsChannel;

export class ScopeController {
  /** Latest parsed audio-scope frame (Svelte 5 reactive). */
  audioScopeFrame: ScopeFrame | null = $state(null);

  /** Hardware scope — not yet implemented; always false for now. */
  readonly hardwareScopeAvailable = false;
  readonly audioScopeAvailable = true;
  readonly activeScope: 'hardware' | 'audio-fft' | null = 'audio-fft';
  readonly scopeFrame: ScopeFrame | null = null;

  private _subscribers = new Map<number, FrameHandler>();
  private _nextId = 0;
  private _bindings = new Map<WsChannel, () => void>();
  private _activeChannel: WsChannel | null = null;
  private _getChannel: ChannelFactory;
  readonly audioFftDriver: PresentationResourceDriver<unknown> = {
    start: () => this._connect(),
    stop: (handle) => this._disconnect(handle as WsChannel),
    dispose: (handle) => this._disconnect(handle as WsChannel),
  };

  constructor(channelFactory: ChannelFactory = getChannel) {
    this._getChannel = channelFactory;
  }

  /**
   * Subscribe to parsed scope frames.
   * Channel lifetime is owned by presentation resource demand.
   * Returns an `unsubscribe` function — call it to stop receiving frames.
   * Each subscribe() call creates an independent subscription, even for the same handler reference.
   */
  subscribe(handler: FrameHandler): () => void {
    const id = this._nextId++;
    this._subscribers.set(id, handler);
    return () => { this._subscribers.delete(id); };
  }

  registerPresentationDriver(host: Pick<PresentationResourceHost<unknown>, 'configure'>): void {
    host.configure('audio-fft', {
      available: this.audioScopeAvailable,
      selected: this.activeScope === 'audio-fft',
      driver: this.audioFftDriver,
    });
  }

  private _connect(): WsChannel {
    const ch = this._getChannel('audio-scope');
    try {
      ch.connect('/api/v1/audio-scope');
      const unsubscribe = ch.onBinary((buf: ArrayBuffer) => {
        if (this._activeChannel !== ch) return;
        markScopeFrame();
        const frame = parseScopeFrame(buf);
        if (frame) {
          this.audioScopeFrame = frame;
          for (const h of this._subscribers.values()) {
            h(frame);
          }
        }
      });
      this._bindings.set(ch, unsubscribe);
      this._activeChannel = ch;
      return ch;
    } catch (error) {
      ch.disconnect();
      throw error;
    }
  }

  private _disconnect(ch: WsChannel): void {
    const unsubscribe = this._bindings.get(ch);
    if (!unsubscribe) return;
    this._bindings.delete(ch);
    try {
      unsubscribe();
    } finally {
      try {
        ch.disconnect();
      } finally {
        if (this._activeChannel === ch) {
          this._activeChannel = null;
          this.audioScopeFrame = null;
        }
      }
    }
  }
}

/** Singleton instance used by FrontendRuntime. */
export const scopeController = new ScopeController();
