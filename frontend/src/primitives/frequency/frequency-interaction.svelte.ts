import { adjustFreqByDigit, type DigitInfo } from './frequency-tuning';

export interface FrequencyInteractionInput {
  readonly confirmedHz: number | null;
  readonly digits: readonly DigitInfo[];
  readonly disabled: boolean;
  readonly contextKey?: string;
  readonly receiver?: 'main' | 'sub';
  readonly minFreq: number;
  readonly maxFreq: number;
  readonly onFreqChange?: (frequencyHz: number) => void;
}

export interface FrequencyInteraction {
  readonly inert: boolean;
  readonly selectedDigitIndex: number | null;
  readonly hoveredDigitIndex: number | null;
  handleWheel(digit: DigitInfo, event: WheelEvent): void;
  handleDigitClick(digit: DigitInfo, event: MouseEvent): void;
  handleKeyDown(event: KeyboardEvent): void;
  handleDigitEnter(digit: DigitInfo): void;
  handleDigitLeave(): void;
  isSelected(digit: DigitInfo): boolean;
  isHovered(digit: DigitInfo): boolean;
}

export interface FrequencyInteractionLease {
  readonly interaction: FrequencyInteraction;
  revoke(): void;
}

const resetInteraction = new WeakMap<FrequencyInteraction, () => void>();
const activeRevocation = new WeakMap<FrequencyInteraction, () => void>();

export function createFrequencyInteraction(
  current: FrequencyInteractionInput,
): FrequencyInteraction {
  let selectedDigitIndex = $state<number | null>(null);
  let hoveredDigitIndex = $state<number | null>(null);
  let inert = $derived.by(() => {
    const { confirmedHz, disabled } = current;
    return disabled || confirmedHz === null || !Number.isFinite(confirmedHz);
  });

  $effect(() => {
    const { contextKey, receiver } = current;
    void inert;
    void contextKey;
    void receiver;
    selectedDigitIndex = null;
    hoveredDigitIndex = null;
  });

  function emitStep(digit: DigitInfo, direction: 1 | -1): void {
    const { confirmedHz, minFreq, maxFreq, onFreqChange } = current;
    if (inert || confirmedHz === null) return;
    const nextHz = adjustFreqByDigit(confirmedHz, digit.multiplier, direction, minFreq, maxFreq);
    if (nextHz !== confirmedHz) onFreqChange?.(nextHz);
  }

  function handleWheel(digit: DigitInfo, event: WheelEvent): void {
    if (inert || selectedDigitIndex !== digit.digitIndex) return;
    event.preventDefault();
    emitStep(digit, event.deltaY > 0 ? -1 : 1);
  }

  function handleDigitClick(digit: DigitInfo, event: MouseEvent): void {
    if (inert) return;
    event.stopPropagation();
    selectedDigitIndex = digit.digitIndex;
  }

  function handleKeyDown(event: KeyboardEvent): void {
    if (inert || selectedDigitIndex === null) return;
    const digit = current.digits.find((candidate) => candidate.digitIndex === selectedDigitIndex);
    if (!digit || (event.key !== 'ArrowUp' && event.key !== 'ArrowDown')) return;
    event.preventDefault();
    emitStep(digit, event.key === 'ArrowUp' ? 1 : -1);
  }

  function handleDigitEnter(digit: DigitInfo): void {
    if (!inert) hoveredDigitIndex = digit.digitIndex;
  }

  function handleDigitLeave(): void {
    hoveredDigitIndex = null;
  }

  function reset(): void {
    selectedDigitIndex = null;
    hoveredDigitIndex = null;
  }

  const interaction: FrequencyInteraction = {
    get inert() { return inert; },
    get selectedDigitIndex() { return selectedDigitIndex; },
    get hoveredDigitIndex() { return hoveredDigitIndex; },
    handleWheel,
    handleDigitClick,
    handleKeyDown,
    handleDigitEnter,
    handleDigitLeave,
    isSelected: (digit) => selectedDigitIndex === digit.digitIndex,
    isHovered: (digit) => hoveredDigitIndex === digit.digitIndex,
  };
  resetInteraction.set(interaction, reset);
  return interaction;
}

export function createFrequencyInteractionLease(
  owner: FrequencyInteraction,
  isCurrent: () => boolean,
): FrequencyInteractionLease {
  let revoked = false;
  function active(): boolean {
    return !revoked && activeRevocation.get(owner) === revoke && isCurrent();
  }
  function revoke(): void {
    if (revoked) return;
    revoked = true;
    if (activeRevocation.get(owner) !== revoke) return;
    activeRevocation.delete(owner);
    resetInteraction.get(owner)?.();
  }

  activeRevocation.get(owner)?.();
  activeRevocation.set(owner, revoke);
  const interaction: FrequencyInteraction = {
    get inert() { return !active() || owner.inert; },
    get selectedDigitIndex() { return active() ? owner.selectedDigitIndex : null; },
    get hoveredDigitIndex() { return active() ? owner.hoveredDigitIndex : null; },
    handleWheel(digit, event) { if (active()) owner.handleWheel(digit, event); },
    handleDigitClick(digit, event) { if (active()) owner.handleDigitClick(digit, event); },
    handleKeyDown(event) { if (active()) owner.handleKeyDown(event); },
    handleDigitEnter(digit) { if (active()) owner.handleDigitEnter(digit); },
    handleDigitLeave() { if (active()) owner.handleDigitLeave(); },
    isSelected: (digit) => active() && owner.isSelected(digit),
    isHovered: (digit) => active() && owner.isHovered(digit),
  };
  return { interaction, revoke };
}
