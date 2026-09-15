import type { ManagedTxState } from './managed-state';

export type ManagedOperation = 'transmit_on' | 'force_off';
export type PttOperation = 'ptt_on' | 'ptt_off';
type Outcome = 'accepted' | 'rejected';

export interface ManagedTxDependencies {
  snapshot(): ManagedTxState;
  refresh(): Promise<void>;
  invalidate(): void;
  sendPtt(operation: PttOperation): Promise<Outcome>;
  submit(operation: ManagedOperation): Promise<Outcome>;
  /** Persists TOT and owns canonical projection invalidation on failure. */
  setTot(configuredSeconds: number | null): Promise<void>;
  /** Browser-only presentation clock; never a TX or transport authority. */
  onPresentationTick?(handler: () => void): () => void;
  /** Server-authority invalidation signal; browser transport seam only. */
  onAuthorityChanged?(handler: () => void): () => void;
  /** Monotonic revision of the last canonical snapshot the store applied. */
  snapshotRevision?(): number;
  startAudio(): Promise<string | null>;
  stopLocalAudio(): void;
  onAudioDied(handler: () => void): () => void;
}

/** Cleanup fence for the ON cycle a superseding server RX snapshot may end. */
type OnCycle = { pending: true } | { cycle: number } | null;

/** Gesture/media orchestration around server-owned TX state. */
export class ManagedTxController {
  #state: ManagedTxState;
  #listeners = new Set<(state: ManagedTxState) => void>();
  #generation = 0;
  #flow: 'idle' | 'ptt' | 'transmit' = 'idle';
  #pttAttempted = false;
  #onCycle: OnCycle = null;
  #onCycleSerial = 0;
  #audioPreparation: Promise<boolean> | null = null;
  #audioNeedsCleanup = false;
  #forceOffInFlight: Promise<void> | null = null;
  #offAudioDied: () => void;
  #offPresentationTick: () => void;
  #offAuthorityChanged: () => void;

  constructor(private readonly dependencies: ManagedTxDependencies) {
    this.#state = dependencies.snapshot();
    this.#offAudioDied = dependencies.onAudioDied(() => { void this.forceOff(); });
    this.#offPresentationTick = dependencies.onPresentationTick?.(() => this.#publish()) ?? (() => {});
    this.#offAuthorityChanged = dependencies.onAuthorityChanged?.(() => { void this.refresh(); })
      ?? (() => {});
  }

  snapshot(): ManagedTxState { return this.#state; }

  subscribe(listener: (state: ManagedTxState) => void): () => void {
    this.#listeners.add(listener);
    return () => this.#listeners.delete(listener);
  }

  async refresh(): Promise<void> {
    const onCycle = this.#onCycle;
    const revision = this.dependencies.snapshotRevision?.() ?? null;
    try { await this.dependencies.refresh(); } catch { this.dependencies.invalidate(); }
    this.#publish();
    if (
      onCycle !== null && 'cycle' in onCycle && onCycle === this.#onCycle
      && this.#flow !== 'idle'
      && (revision === null || (this.dependencies.snapshotRevision?.() ?? revision) > revision)
    ) {
      const state = this.dependencies.snapshot();
      if (state.fresh && state.intent === null) this.#cleanupSupersededOn();
    }
  }

  invalidate(): void {
    this.dependencies.invalidate();
    this.#publish();
  }

  pttOn(): void {
    const generation = ++this.#generation;
    this.#flow = 'ptt';
    this.#pttAttempted = false;
    this.#onCycle = { pending: true };
    void this.#sendPttOn(generation);
  }

  async pttOff(): Promise<void> {
    ++this.#generation;
    this.#flow = 'idle';
    const attempted = this.#pttAttempted;
    this.#pttAttempted = false;
    this.#onCycle = null;
    this.#stopAudio();
    try {
      if (attempted) await this.dependencies.sendPtt('ptt_off');
    } catch { this.dependencies.invalidate(); }
    finally { this.#publish(); }
  }

  transmitOn(): void {
    if (!this.dependencies.snapshot().fresh) return;
    const generation = ++this.#generation;
    this.#flow = 'transmit';
    this.#onCycle = { pending: true };
    void this.#sendTransmitOn(generation);
  }

  async forceOff(): Promise<void> {
    if (this.#forceOffInFlight !== null) return this.#forceOffInFlight;
    this.#forceOffInFlight = this.#forceOff();
    try { await this.#forceOffInFlight; }
    finally { this.#forceOffInFlight = null; }
  }

  async setTot(configuredSeconds: number | null): Promise<void> {
    if (configuredSeconds !== null
      && (!Number.isFinite(configuredSeconds) || configuredSeconds <= 0)) {
      throw new RangeError('Managed TOT must be a positive finite number or null');
    }
    try {
      await this.dependencies.setTot(configuredSeconds);
    } catch (error) {
      this.#publish();
      throw error;
    }
    this.#publish();
  }

  async #forceOff(): Promise<void> {
    ++this.#generation;
    this.#flow = 'idle';
    this.#pttAttempted = false;
    this.#onCycle = null;
    try { await this.dependencies.submit('force_off'); }
    catch { this.dependencies.invalidate(); }
    finally { this.#stopAudio(); this.#publish(); }
  }

  async releaseSession(): Promise<void> {
    if (this.#flow === 'ptt' || this.#pttAttempted) await Promise.race([
      this.pttOff(), new Promise<void>((resolve) => setTimeout(resolve, 500)),
    ]);
    else { ++this.#generation; this.#flow = 'idle'; this.#onCycle = null; this.#stopAudio(); }
  }

  abandonSession(): void {
    ++this.#generation;
    this.#flow = 'idle';
    this.#pttAttempted = false;
    this.#onCycle = null;
    this.#stopAudio();
    this.invalidate();
  }

  dispose(): void {
    ++this.#generation;
    this.#offAuthorityChanged();
    this.#offAudioDied();
    this.#offPresentationTick();
    this.#listeners.clear();
    this.#flow = 'idle';
    this.#pttAttempted = false;
    this.#onCycle = null;
    this.#stopAudio();
  }

  async #sendPttOn(generation: number): Promise<void> {
    if (!await this.#prepareAudio()) return;
    if (generation !== this.#generation || this.#flow !== 'ptt') return;
    this.#pttAttempted = true;
    let outcome: Outcome;
    try { outcome = await this.dependencies.sendPtt('ptt_on'); }
    catch { outcome = 'rejected'; }
    if (generation !== this.#generation || this.#flow !== 'ptt') return;
    if (outcome === 'accepted') {
      this.#completeOnCycle();
    } else {
      this.#onCycle = null;
      this.#pttAttempted = false;
      this.#flow = 'idle';
      this.#stopAudio();
    }
    this.#publish();
  }

  async #sendTransmitOn(generation: number): Promise<void> {
    if (!await this.#prepareAudio()) return;
    if (generation !== this.#generation || this.#flow !== 'transmit') return;
    if (this.#pttAttempted) {
      let released: Outcome;
      try { released = await this.dependencies.sendPtt('ptt_off'); }
      catch { released = 'rejected'; this.dependencies.invalidate(); }
      if (generation !== this.#generation || this.#flow !== 'transmit') return;
      if (released === 'rejected') {
        this.#onCycle = null;
        this.#flow = 'idle';
        this.#stopAudio();
        this.#publish();
        return;
      }
      this.#pttAttempted = false;
    }
    let outcome: Outcome;
    try { outcome = await this.dependencies.submit('transmit_on'); }
    catch { outcome = 'rejected'; this.dependencies.invalidate(); }
    if (generation !== this.#generation || this.#flow !== 'transmit') return;
    if (outcome === 'accepted') {
      this.#completeOnCycle();
    } else {
      this.#onCycle = null;
      this.#flow = 'idle';
      this.#stopAudio();
    }
    this.#publish();
  }

  #completeOnCycle(): void {
    this.#onCycle = { cycle: ++this.#onCycleSerial };
    void this.refresh();
  }

  #cleanupSupersededOn(): void {
    ++this.#generation;
    this.#flow = 'idle';
    this.#pttAttempted = false;
    this.#onCycle = null;
    this.#stopAudio();
    this.#publish();
  }

  #prepareAudio(): Promise<boolean> {
    if (this.#audioPreparation !== null) return this.#audioPreparation;
    this.#audioNeedsCleanup = true;
    this.#audioPreparation = (async () => {
      try { if (await this.dependencies.startAudio() === null) return true; }
      catch { /* local media refusal */ }
      this.#stopAudio();
      return false;
    })();
    return this.#audioPreparation;
  }

  #stopAudio(): void {
    this.#audioPreparation = null;
    if (!this.#audioNeedsCleanup) return;
    this.#audioNeedsCleanup = false;
    this.dependencies.stopLocalAudio();
  }

  #publish(): void {
    this.#state = this.dependencies.snapshot();
    for (const listener of this.#listeners) listener(this.#state);
  }
}
