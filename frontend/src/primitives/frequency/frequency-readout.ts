import {
  groupDigitsForDisplay,
  splitFrequencyToDigits,
  type DigitInfo,
} from './frequency-tuning';

export type FrequencyReadoutSource = 'confirmed' | 'display' | 'pending';
export type FrequencyReadoutStatus = 'confirmed' | 'pending';

export interface FrequencyReadoutInput {
  confirmedHz: number | null;
  displayHz?: number | null;
  pendingDisplayHz?: number | null;
  pendingAnnouncement?: string;
}

export interface FrequencyReadoutModel {
  readonly confirmedHz: number | null;
  readonly displayHz: number | null;
  readonly pendingDisplayHz: number | null;
  readonly shownHz: number | null;
  readonly source: FrequencyReadoutSource;
  readonly status: FrequencyReadoutStatus;
  readonly known: boolean;
  readonly digits: readonly DigitInfo[];
  readonly groups: Readonly<{
    mhz: readonly DigitInfo[];
    khz: readonly DigitInfo[];
    hz: readonly DigitInfo[];
  }>;
  readonly textGroups: Readonly<{ mhz: string; khz: string; hz: string }>;
  readonly pendingAnnouncement?: string;
}

export function projectFrequencyReadout(input: FrequencyReadoutInput): FrequencyReadoutModel {
  const explicitDisplay = input.displayHz !== undefined;
  const displayHz = explicitDisplay ? input.displayHz ?? null : input.confirmedHz;
  const pendingDisplayHz = input.pendingDisplayHz ?? null;
  const source: FrequencyReadoutSource = pendingDisplayHz !== null
    ? 'pending'
    : explicitDisplay ? 'display' : 'confirmed';
  const shownHz = pendingDisplayHz ?? displayHz;
  const known = shownHz !== null && Number.isFinite(shownHz);
  const digits = known ? splitFrequencyToDigits(shownHz) : [];
  const groups = groupDigitsForDisplay(digits);

  return {
    confirmedHz: input.confirmedHz,
    displayHz,
    pendingDisplayHz,
    shownHz,
    source,
    status: source === 'pending' ? 'pending' : 'confirmed',
    known,
    digits,
    groups,
    textGroups: known
      ? {
          mhz: groups.mhz.map((digit) => digit.char).join('') || '0',
          khz: groups.khz.map((digit) => digit.char).join(''),
          hz: groups.hz.map((digit) => digit.char).join(''),
        }
      : { mhz: '--', khz: '---', hz: '---' },
    pendingAnnouncement: input.pendingAnnouncement,
  };
}
