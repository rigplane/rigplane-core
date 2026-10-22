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
  private _localSessions = false;
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
  get session(): boolean { return this.#grant !== null; }
  get sessionMode(): boolean { return this._remote || this._localSessions; }
  get active(): boolean {
    return this.prepared && (this.#grant === null || !this._remote || this.primaryReady);
  }

  valid(epoch: number): boolean {
    return this.#grant !== null ? epoch === this._epoch : !this._remote;
  }

  current(epoch: number): boolean {
    return this.valid(epoch) && (this.#grant === null || this.active);
  }

  headers(): Record<string, string> {
    return this.#grant ? { 'X-RigPlane-Controller': this.#grant.controller_key } : {};
  }

  protocols(): string[] {
    if (this.#grant === null) {
      if (!this._remote) return [];
      throw new ControllerClientError('controller_lost');
    }
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
    const operation = Promise.resolve().then(() => this.acquire(epoch, signal)).catch((error: unknown) => {
      if (error instanceof ControllerClientError || signal?.aborted) throw error;
      throw new ControllerClientError('controller_not_ready');
    }).finally(() => {
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
    this._remote = !this._localSessions;
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
      this.changed();
      // MOR-3535: local mode grants every browser its own session. An older
      // core that refuses the POST (or a transient failure) keeps today's
      // sessionless behaviour instead of failing the UI.
      let granted: { status: number; value: unknown } | null = null;
      try {
        granted = await this.postBrowser(signal);
        this.check(epoch, signal);
      } catch (error) {
        if (signal?.aborted || error instanceof ControllerClientError) throw error;
      }
      if (granted !== null && granted.status === 201 && this.adopt(granted.value)) {
        this._localSessions = true;
      }
      this.prepared = true;
      this.changed();
      return;
    }
    this._remote = true;
    const { status: grantedStatus, value } = await this.postBrowser(signal);
    this.check(epoch, signal);
    if (grantedStatus !== 201) {
      const code = record(value) && typeof value.error === 'string'
        && Object.hasOwn(messages, value.error) ? value.error as Code : 'controller_not_ready';
      throw new ControllerClientError(code);
    }
    if (!this.adopt(value)) throw new ControllerClientError('controller_invalid');
    this.changed();
  }

  private async postBrowser(signal?: AbortSignal): Promise<{ status: number; value: unknown }> {
    const response = await fetch('/api/v1/controller', {
      method: 'POST', signal, redirect: 'error',
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ kind: 'browser' }),
    });
    return { status: response.status, value: await response.json() };
  }

  private adopt(value: unknown): boolean {
    if (!record(value) || typeof value.controller_key !== 'string'
      || !/^[0-9a-f]{64}$/.test(value.controller_key)
      || typeof value.generation !== 'number' || !Number.isSafeInteger(value.generation) || value.generation < 0
      || value.ttl_ms !== 6000 || value.heartbeat_ms !== 2000
      || (value.controller_role !== undefined && value.controller_role !== 'primary' && value.controller_role !== 'auxiliary')) {
      return false;
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
    return true;
  }

  waitForPrimary(): Promise<void> {
    if (!this._remote && this.#grant === null) return Promise.resolve();
    if (this.#grant !== null && this.primaryReady) return Promise.resolve();
    if (this.#grant === null) return Promise.reject(new ControllerClientError('controller_lost'));
    return new Promise<void>((resolve, reject) => { this.children.add({ resolve, reject }); });
  }

  controlOpened(sender: Sender): void {
    if (this.#grant === null) return;
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
    if (!response.ok && response.status !== 403) {
      // A local session release is best-effort: the server TTL reaps it.
      if (!this._remote) return;
      throw new ControllerClientError('controller_not_ready');
    }
  }
}

export const controllerClient = new ControllerClient();
