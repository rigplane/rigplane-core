export interface AgcOption {
  value: number;
  label: string;
}

/**
 * Builds the options array for the AGC mode SegmentedButton.
 *
 * MOR-1522: the AGC option set is radio-specific profile data (declared in
 * the rig TOML's `[agc] modes`/`labels`) — this renders ONLY the declared
 * options, in declaration order. It must NOT invent an OFF option: the
 * IC-7300/IC-7610/IC-705/IC-9700 family has no AGC OFF at all (FAST/MID/SLOW
 * only), while the X6200 and FTX-1 do declare OFF as one of their modes.
 * Modes without a matching label fall back to their string representation.
 */
export function buildAgcOptions(
  modes: number[],
  labels: Record<string, string>,
): AgcOption[] {
  return modes.map((mode) => ({
    value: mode,
    label: labels[String(mode)] ?? String(mode),
  }));
}
