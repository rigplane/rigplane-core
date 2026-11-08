/**
 * Stand-in for `getActiveFrequencyHz()` in StatusBar tests: a reactive read,
 * like the real one (it reads `runtime.state`), whose `deliver()` simulates a
 * state delivery that leaves the active frequency unchanged.
 */
let deliveries = $state(0);

export const activeFrequencyProbe = {
  frequencyHz: 14_074_000 as number | null,
  read(): number | null {
    void deliveries;
    return activeFrequencyProbe.frequencyHz;
  },
  deliver(): void {
    deliveries += 1;
  },
};
