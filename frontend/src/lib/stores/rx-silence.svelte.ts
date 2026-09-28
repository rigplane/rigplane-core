// MOR-2792: server RX capture is bit-exact digital silence.
// Own module so existing connection-store test mocks stay untouched.
let rxSilent = $state(false);

export function setRxSilent(v: boolean): void {
  rxSilent = v;
}

export function isRxSilent(): boolean {
  return rxSilent;
}
