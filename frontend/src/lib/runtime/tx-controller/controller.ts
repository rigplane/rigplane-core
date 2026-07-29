import { initialTxState, transition, type PttMarker, type PttObservation, type TxEffect, type TxEvent, type TxGuard, type TxState, type TxTarget } from './model';
type Command = 'on' | 'off';
type Timer = 'audio-start' | 'on-confirmation' | 'off-confirmation';
type CommandReport = { outcome: 'sent' | 'ack' | 'response-ok' | 'response-error' | 'transport-error'; eventEpoch: number; barrier: PttMarker | null };
type CommandCorrelation = { leaseId: string; generation: number; originalEpoch: number; target: Exclude<TxTarget, null> };
type TimerRecord = { handle: unknown; guard: TxGuard; cancelGuard: TxGuard };
export interface TxControllerDependencies {
  startAudio(): Promise<string | null>;
  sendPtt(command: Command, commandId: string, correlation: CommandCorrelation, report: (result: CommandReport) => void): void;
  stopLocalAudio(): void;
  restoreMod(barrier: PttMarker, observation: PttObservation): void;
  commandId(command: Command): string;
  schedule(callback: () => void, delayMs: number): unknown;
  cancel(handle: unknown): void;
  timeoutMs: Record<Timer, number>;
}
const sameGuard = (a: TxGuard, b: TxGuard) =>
  a.leaseId === b.leaseId && a.generation === b.generation && a.authorityEpoch === b.authorityEpoch;
export class TxController {
  #state: TxState;
  #leaseTarget: Exclude<TxTarget, null> | null = null;
  #timers = new Set<TimerRecord>();
  #listeners = new Set<(state: TxState) => void>();
  #events: TxEvent[] = [];
  #processing = false;
  constructor(authorityEpoch: number, baseline: PttMarker, private readonly dependencies: TxControllerDependencies) {
    this.#state = initialTxState(authorityEpoch, baseline);
  }
  snapshot(): TxState { return this.#state; }
  subscribe(listener: (state: TxState) => void): () => void {
    this.#listeners.add(listener);
    return () => this.#listeners.delete(listener);
  }
  dispatch(event: TxEvent): void {
    const queued: TxEvent = event.type === 'start' && event.eligibility.target !== null
      ? { ...event, eligibility: { ...event.eligibility, target: { ...event.eligibility.target } } }
      : event;
    this.#events.push(queued);
    if (this.#processing) return;
    this.#processing = true;
    try {
      while (this.#events.length) {
        const current = this.#events.shift()!;
        const result = transition(this.#state, current);
        if (current.type === 'start' && current.eligibility.target !== null
          && result.effects.some((effect) => effect.type === 'start-audio')) {
          this.#leaseTarget = { ...current.eligibility.target };
        }
        const retainLeaseTarget = result.state.guard !== null || result.state.pendingOff !== null || result.state.mayOwnKey;
        this.#state = result.state;
        for (const listener of this.#listeners) try { listener(this.#state); } catch { /* observers cannot block safety effects */ }
        this.#run(result.effects, current);
        if (!retainLeaseTarget) this.#leaseTarget = null;
      }
    } finally { this.#processing = false; }
  }
  #run(effects: TxEffect[], cause: TxEvent): void {
    const failures: Array<{ effect: TxEffect; command: Command; offCommandId: string; eventEpoch: number }> = [];
    let stopped = false;
    try {
      for (const effect of effects) {
        if (effect.type === 'stop-local-audio') stopped = true;
        if (effect.type === 'dispatch-on' || effect.type === 'dispatch-off') {
          const command = effect.type === 'dispatch-on' ? 'on' : 'off';
          const offCommandId = command === 'off' ? effect.commandId! : this.dependencies.commandId('off');
          try { this.#send(effect, command, offCommandId); }
          catch { failures.push({ effect, command, offCommandId, eventEpoch: this.#state.authorityEpoch }); }
        } else {
          this.#effect(effect, cause);
        }
      }
    } finally {
      if (!stopped && effects.some((effect) => effect.type === 'stop-local-audio')) this.dependencies.stopLocalAudio();
    }
    for (const failure of failures) this.#commandResult(failure.effect, failure.command, failure.offCommandId, {
      outcome: 'transport-error', eventEpoch: failure.eventEpoch, barrier: null,
    });
  }
  #send(effect: TxEffect, command: Command, offCommandId: string): void {
    const guard = effect.guard!;
    const leaseTarget = this.#leaseTarget;
    if (leaseTarget === null) throw new Error('TX command effect missing lease target');
    const correlation = { leaseId: guard.leaseId, generation: guard.generation, originalEpoch: guard.authorityEpoch, target: { ...leaseTarget } };
    this.dependencies.sendPtt(command, effect.commandId!, correlation,
      (report) => this.#commandResult(effect, command, offCommandId, report));
  }
  #commandResult(effect: TxEffect, command: Command, offCommandId: string, report: CommandReport): void {
    const guard = effect.guard!;
    this.dispatch({ type: 'command-result', command, commandId: effect.commandId!, offCommandId,
      leaseId: guard.leaseId, generation: guard.generation, originalEpoch: guard.authorityEpoch,
      eventEpoch: report.eventEpoch, outcome: report.outcome, barrier: report.barrier });
  }
  #effect(effect: TxEffect, cause: TxEvent): void {
    const guard = effect.guard!;
    if (effect.type === 'start-audio') {
      let pending: Promise<string | null>;
      try { pending = this.dependencies.startAudio(); }
      catch { this.dispatch({ type: 'fail', guard, fault: 'audio-failed', offCommandId: this.dependencies.commandId('off') }); return; }
      void pending.then((error) => this.dispatch(error
        ? { type: 'fail', guard, fault: 'audio-failed', offCommandId: this.dependencies.commandId('off') }
        : { type: 'audio-ready', guard, commandId: this.dependencies.commandId('on') }),
      () => this.dispatch({ type: 'fail', guard, fault: 'audio-failed', offCommandId: this.dependencies.commandId('off') }));
    } else if (effect.type.startsWith('arm-')) {
      const timer = effect.type === 'arm-audio-timeout' ? 'audio-start' : effect.type === 'arm-on-timeout' ? 'on-confirmation' : 'off-confirmation';
      const eventEpoch = this.#state.authorityEpoch;
      const offCommandId = timer === 'off-confirmation' ? effect.commandId! : this.dependencies.commandId('off');
      const record: TimerRecord = { handle: null, guard, cancelGuard: this.#state.cleanupGuard ?? guard };
      let fired = false; const fire = () => { if (fired) return; fired = true; this.#timers.delete(record); this.dispatch({
          type: 'timer-fired', timer, commandId: effect.commandId ?? null, armRevision: effect.armRevision!,
          leaseId: guard.leaseId, generation: guard.generation, originalEpoch: guard.authorityEpoch, eventEpoch, offCommandId });
      };
      this.#timers.add(record); try { record.handle = this.dependencies.schedule(fire, this.dependencies.timeoutMs[timer]); }
      catch { fire(); }
    } else if (effect.type === 'cancel-timers') {
      for (const timer of this.#timers) if (sameGuard(timer.guard, guard) || sameGuard(timer.cancelGuard, guard)) {
        this.#timers.delete(timer);
        try { this.dependencies.cancel(timer.handle); } catch { /* deadline invalidation remains authoritative */ }
      }
    } else if (effect.type === 'stop-local-audio') this.dependencies.stopLocalAudio();
    else if (effect.type === 'restore-mod' && cause.type === 'authority') this.dependencies.restoreMod(effect.barrier!, cause.ptt);
  }
}
