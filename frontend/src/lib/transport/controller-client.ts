type Role = 'primary' | 'auxiliary';
type Grant = { controller_key: string; generation: number; controller_role?: Role };
type Sender = (message: { type: 'controller_heartbeat' }) => void;

const messages = {
  controller_busy: 'Управление занято другим клиентом. Подключитесь после освобождения.',
  controller_invalid: 'Станция отказала в доступе к удалённому управлению.',
  controller_lost: 'Удалённое управление потеряно. Подключитесь заново.',
  controller_not_ready: 'Станция пока не готова к удалённому управлению.',
  controller_unsupported: 'Станция не поддерживает этот протокол управления.',
  controller_local_required: 'Это действие доступно только на локальном хосте.',
  remote_disabled: 'Удалённое управление на хосте выключено.',
};
type Code = keyof typeof messages;

export class ControllerClientError extends Error {
  constructor(readonly code: Code) { super(messages[code]); }
}

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

export class ControllerClient {
  #grant: Grant | null = null;
  private _remote = false;
  private prepared = false;
  private primaryReady = false;
  private _epoch = 0;
  private reportedLoss = false;
  private pending: Promise<void> | null = null;
  private sender: Sender | null = null;
  private lastAck = 0;
  private timer: ReturnType<typeof setInterval> | null = null;
  private losses = new Set<() => void>();
  private changes = new Set<() => void>();
  private children = new Set<{ resolve: () => void; reject: (error: Error) => void }>();

  constructor(private readonly clock = () => performance.now()) {}

  get remote(): boolean { return this._remote; }
  get role(): Role { return this.#grant?.controller_role ?? 'primary'; }
  get epoch(): number { return this._epoch; }
  get active(): boolean { return this.prepared && (!this.remote || this.primaryReady); }

  valid(epoch: number): boolean {
    return !this.remote || (this.#grant !== null && epoch === this._epoch);
  }

  current(epoch: number): boolean { return this.valid(epoch) && (!this.remote || this.active); }

  headers(): Record<string, string> {
    return this.#grant ? { 'X-RigPlane-Controller': this.#grant.controller_key } : {};
  }

  protocols(): string[] {
    if (!this.remote) return [];
    if (!this.#grant) throw new ControllerClientError('controller_lost');
    return [`rigplane-controller-v1.${this.#grant.controller_key}`];
  }

  assertHostAction(): void {
    if (this.remote) throw new ControllerClientError('controller_local_required');
  }

  onLoss(callback: () => void): () => void {
    this.losses.add(callback); return () => { this.losses.delete(callback); };
  }

  onChange(callback: () => void): () => void {
    this.changes.add(callback); return () => { this.changes.delete(callback); };
  }

  private changed(): void {
    for (const callback of [...this.changes]) { try { callback(); } catch { /* isolate subscribers */ } }
  }

  prepare(signal?: AbortSignal): Promise<void> {
    if (this.#grant) return Promise.resolve();
    if (this.pending) return this.pending;
    const epoch = this._epoch;
    const operation = Promise.resolve().then(() => this.acquire(epoch, signal)).finally(() => {
      if (this.pending === operation) this.pending = null;
    });
    this.pending = operation;
    return operation;
  }

  private check(epoch: number, signal?: AbortSignal): void {
    signal?.throwIfAborted();
    if (epoch !== this._epoch) throw new ControllerClientError('controller_lost');
  }

  private async acquire(epoch: number, signal?: AbortSignal): Promise<void> {
    this.check(epoch, signal);
    this.reportedLoss = false;
    this._remote = true;
    this.prepared = false;
    this.changed();
    const statusResponse = await fetch('/api/v1/controller', { signal, redirect: 'error' });
    this.check(epoch, signal);
    const status: unknown = await statusResponse.json();
    if (!statusResponse.ok || !record(status) || status.protocol_version !== 1
      || (status.mode !== 'local' && status.mode !== 'remote')) {
      throw new ControllerClientError('controller_unsupported');
    }
    this.check(epoch, signal);
    if (status.mode === 'local') {
      this._remote = false;
      this.prepared = true;
      this.changed();
      return;
    }
    const response = await fetch('/api/v1/controller', {
      method: 'POST', signal, redirect: 'error',
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ kind: 'browser' }),
    });
    const value: unknown = await response.json();
    this.check(epoch, signal);
    if (response.status !== 201) {
      const code = record(value) && typeof value.error === 'string'
        && Object.hasOwn(messages, value.error) ? value.error as Code : 'controller_not_ready';
      throw new ControllerClientError(code);
    }
    if (!record(value) || typeof value.controller_key !== 'string'
      || !/^[0-9a-f]{64}$/.test(value.controller_key)
      || typeof value.generation !== 'number' || !Number.isSafeInteger(value.generation) || value.generation < 0
      || value.ttl_ms !== 6000 || value.heartbeat_ms !== 2000
      || (value.controller_role !== undefined && value.controller_role !== 'primary' && value.controller_role !== 'auxiliary')) {
      throw new ControllerClientError('controller_invalid');
    }
    this.#grant = { controller_key: value.controller_key, generation: value.generation,
      controller_role: value.controller_role ?? 'primary' };
    this._epoch += 1;
    this.prepared = true;
    this.primaryReady = this.role === 'auxiliary';
    this.lastAck = this.clock();
    if (this.role === 'primary') this.timer = setInterval(() => {
      if (this.clock() - this.lastAck >= 6000) this.lose();
      else if (this.primaryReady) this.sendHeartbeat();
    }, 2000);
    this.changed();
  }

  waitForPrimary(): Promise<void> {
    if (!this.remote || this.active) return Promise.resolve();
    if (!this.#grant) return Promise.reject(new ControllerClientError('controller_lost'));
    return new Promise<void>((resolve, reject) => { this.children.add({ resolve, reject }); });
  }

  controlOpened(sender: Sender): void {
    if (!this.remote || !this.#grant) return;
    this.primaryReady = true;
    if (this.role === 'primary') { this.sender = sender; this.sendHeartbeat(); }
    for (const child of this.children) child.resolve();
    this.children.clear();
    this.changed();
  }

  private sendHeartbeat(): void {
    try { this.sender?.({ type: 'controller_heartbeat' }); } catch { this.lose(); }
  }

  acknowledge(message: Record<string, unknown>): boolean {
    if (message.type !== 'controller_heartbeat') return false;
    if (this.#grant && message.generation !== this.#grant.generation) this.lose();
    else if (this.#grant && this.role === 'primary') this.lastAck = this.clock();
    return true;
  }

  private invalidate(): void {
    this._epoch += 1;
    this.#grant = null;
    this.prepared = false;
    this.primaryReady = false;
    this.sender = null;
    if (this.timer !== null) clearInterval(this.timer);
    this.timer = null;
    for (const child of this.children) child.reject(new ControllerClientError('controller_lost'));
    this.children.clear();
    this.changed();
  }

  lose(): void {
    if (this.reportedLoss) return;
    this.reportedLoss = true;
    this.invalidate();
    for (const callback of [...this.losses]) { try { callback(); } catch { /* isolate subscribers */ } }
  }

  async release(): Promise<void> {
    const grant = this.#grant;
    this.reportedLoss = true;
    this.invalidate();
    if (!grant || grant.controller_role === 'auxiliary') return;
    const response = await fetch('/api/v1/controller', {
      method: 'DELETE', headers: { 'X-RigPlane-Controller': grant.controller_key }, redirect: 'error',
    });
    if (!response.ok && response.status !== 403) throw new ControllerClientError('controller_not_ready');
  }
}

export const controllerClient = new ControllerClient();
