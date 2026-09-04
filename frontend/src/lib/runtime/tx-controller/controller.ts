import type { TxState } from './model';

export type PttIntentOperation = 'ptt_on' | 'ptt_off';
export type ManagedTransmitOperation = 'transmit_on' | 'force_off';

export interface TxControllerDependencies {
  snapshot(): TxState;
  refresh(): Promise<void>;
  invalidate(): void;
  sendPtt(operation: PttIntentOperation): Promise<'accepted' | 'rejected'>;
  submit(operation: ManagedTransmitOperation): Promise<'accepted' | 'rejected'>;
  startAudio(): Promise<string | null>;
  stopLocalAudio(): void;
  onAudioDied(handler: () => void): () => void;
}

/** Browser thin client for server-owned ManagedTxAuthority. */
export class TxController {
  #state: TxState;
  #listeners = new Set<(state: TxState) => void>();
  #inputGeneration = 0;
  #flow: 'idle' | 'ptt' | 'transmit' = 'idle';
  #pttOnAttempted = false;
  #audioPreparation: Promise<boolean> | null = null;
  #audioNeedsCleanup = false;
  #offAudioDied: () => void;

  constructor(private readonly dependencies: TxControllerDependencies) {
    this.#state = dependencies.snapshot();
    this.#offAudioDied = dependencies.onAudioDied(() => { void this.forceOff(); });
  }

  snapshot(): TxState { return this.#state; }

  subscribe(listener: (state: TxState) => void): () => void {
    this.#listeners.add(listener);
    return () => this.#listeners.delete(listener);
  }

  async refresh(): Promise<void> {
    try { await this.dependencies.refresh(); } catch { this.dependencies.invalidate(); }
    this.#publish(this.dependencies.snapshot());
  }

  invalidate(): void {
    this.dependencies.invalidate();
    this.#publish(this.dependencies.snapshot());
  }

  /** Momentary, session-owned PTT intent over the control WebSocket. */
  requestPtt(): void {
    const generation = ++this.#inputGeneration;
    this.#flow = 'ptt';
    this.#pttOnAttempted = false;
    void this.#submitPttOn(generation);
  }

  /** Ordinary release of this browser session's momentary PTT intent. */
  async releasePtt(): Promise<void> {
    ++this.#inputGeneration;
    this.#flow = 'idle';
    const attempted = this.#pttOnAttempted;
    this.#pttOnAttempted = false;
    try {
      if (attempted) await this.dependencies.sendPtt('ptt_off');
    } catch { this.dependencies.invalidate(); }
    finally {
      this.#stopLocalAudio();
      this.#publish(this.dependencies.snapshot());
    }
  }

  /** Latched TRANSMIT intent through the canonical managed-transmit client. */
  requestTransmit(): void {
    const generation = ++this.#inputGeneration;
    this.#flow = 'transmit';
    this.#pttOnAttempted = false;
    void this.#submitTransmitOn(generation);
  }

  /** Unconditional server ForceOFF intent, distinct from ordinary PTT release. */
  async forceOff(): Promise<void> {
    ++this.#inputGeneration;
    this.#flow = 'idle';
    this.#pttOnAttempted = false;
    try { await this.dependencies.submit('force_off'); }
    catch { this.dependencies.invalidate(); }
    finally {
      this.#stopLocalAudio();
      this.#publish(this.dependencies.snapshot());
    }
  }

  async releaseSession(): Promise<void> {
    if (this.#flow === 'ptt' || this.#pttOnAttempted) await this.releasePtt();
    else {
      ++this.#inputGeneration;
      this.#flow = 'idle';
      this.#stopLocalAudio();
    }
  }

  abandonSession(): void {
    ++this.#inputGeneration;
    this.#flow = 'idle';
    this.#pttOnAttempted = false;
    this.#stopLocalAudio();
    this.invalidate();
  }

  dispose(): void {
    ++this.#inputGeneration;
    this.#offAudioDied();
    this.#listeners.clear();
    this.#flow = 'idle';
    this.#pttOnAttempted = false;
    this.#stopLocalAudio();
  }

  async #submitPttOn(generation: number): Promise<void> {
    if (!await this.#prepareAudio()) return;
    if (generation !== this.#inputGeneration || this.#flow !== 'ptt') return;
    this.#pttOnAttempted = true;
    let outcome: 'accepted' | 'rejected';
    try { outcome = await this.dependencies.sendPtt('ptt_on'); }
    catch { outcome = 'rejected'; }
    if (generation !== this.#inputGeneration || this.#flow !== 'ptt') return;
    if (outcome === 'rejected') {
      this.#pttOnAttempted = false;
      this.#flow = 'idle';
      this.#stopLocalAudio();
    }
    this.#publish(this.dependencies.snapshot());
  }

  async #submitTransmitOn(generation: number): Promise<void> {
    if (!await this.#prepareAudio()) return;
    if (generation !== this.#inputGeneration || this.#flow !== 'transmit') return;
    try {
      const outcome = await this.dependencies.submit('transmit_on');
      if (outcome === 'rejected' && generation === this.#inputGeneration) {
        this.#flow = 'idle';
        this.#stopLocalAudio();
      }
    } catch {
      if (generation === this.#inputGeneration) {
        this.#flow = 'idle';
        this.#stopLocalAudio();
      }
      this.dependencies.invalidate();
    }
    this.#publish(this.dependencies.snapshot());
  }

  #prepareAudio(): Promise<boolean> {
    if (this.#audioPreparation !== null) return this.#audioPreparation;
    this.#audioNeedsCleanup = true;
    this.#audioPreparation = (async () => {
      try {
        if (await this.dependencies.startAudio() === null) return true;
      } catch { /* local media refusal */ }
      this.#stopLocalAudio();
      return false;
    })();
    return this.#audioPreparation;
  }

  #stopLocalAudio(): void {
    this.#audioPreparation = null;
    if (!this.#audioNeedsCleanup) return;
    this.#audioNeedsCleanup = false;
    this.dependencies.stopLocalAudio();
  }

  #publish(next: TxState): void {
    this.#state = next;
    for (const listener of this.#listeners) listener(next);
  }
}
