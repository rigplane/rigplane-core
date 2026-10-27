/**
 * Utility functions for the RF Front End panel.
 */

export interface AttOption {
  value: number;
  label: string;
}

export interface PreOption {
  value: number;
  label: string;
}

export interface AttControlModel {
  quickOptions: AttOption[];
  overflowOptions: AttOption[];
}

const ATT_QUICK_VALUES = [0, 6, 12, 18];

/** Format an ATT value to a display label, using custom labels if provided. */
export function formatAttLabel(value: number, labels?: Record<string, string>): string {
  if (labels && String(value) in labels) return labels[String(value)];
  return value === 0 ? 'OFF' : `${value}dB`;
}

/** Format a PRE level to a display label, using custom labels if provided. */
export function formatPreLabel(level: number, labels?: Readonly<Record<string, string>>): string {
  if (labels && String(level) in labels) return labels[String(level)];
  return level === 0 ? 'OFF' : `P${level}`;
}

/** Build SegmentedButton options for the ATT control from a list of values. */
export function buildAttOptions(values: number[], labels?: Record<string, string>): AttOption[] {
  return values.map((v) => ({ value: v, label: formatAttLabel(v, labels) }));
}

/** Build a hybrid ATT control model with quick picks and overflow values. */
export function buildAttControlModel(values: number[], labels?: Record<string, string>): AttControlModel {
  const normalized = [...new Set(values)];

  if (normalized.length <= ATT_QUICK_VALUES.length) {
    return {
      quickOptions: buildAttOptions(normalized, labels),
      overflowOptions: [],
    };
  }

  const preferredQuick = ATT_QUICK_VALUES.filter((value) => normalized.includes(value));
  const remainingQuickSlots = Math.max(0, ATT_QUICK_VALUES.length - preferredQuick.length);
  const fillValues = normalized
    .filter((value) => !preferredQuick.includes(value))
    .slice(0, remainingQuickSlots);
  const quickValues = [...preferredQuick, ...fillValues];
  const quickSet = new Set(quickValues);

  return {
    quickOptions: buildAttOptions(quickValues, labels),
    overflowOptions: buildAttOptions(normalized.filter((value) => !quickSet.has(value)), labels),
  };
}

/** Return the overflow button label for the currently selected ATT value. */
export function getAttOverflowLabel(selected: number, overflowOptions: AttOption[]): string {
  return overflowOptions.find((option) => option.value === selected)?.label ?? 'MORE';
}

/** Build SegmentedButton options for the PRE control from a list of levels. */
export function buildPreOptions(values: number[], labels?: Record<string, string>): PreOption[] {
  return values.map((v) => ({ value: v, label: formatPreLabel(v, labels) }));
}

/**
 * Determine whether the RF Front End panel should be visible at all.
 * The panel is hidden when none of its controls are available.
 */
export function shouldShowPanel(
  hasRfGain: boolean,
  hasAtt: boolean,
  hasPre: boolean,
  hasSquelch = false,
): boolean {
  return hasRfGain || hasSquelch || hasAtt || hasPre;
}
