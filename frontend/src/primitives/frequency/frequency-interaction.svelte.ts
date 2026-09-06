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
  readonly revoked: boolean;
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
  let resetGeneration = 0;
  function clearOwner(): void {
    resetGeneration += 1;
    resetInteraction.get(owner)?.();
  }
  function deferOwnerReset(): void {
    const generation = ++resetGeneration;
    queueMicrotask(() => {
      if (generation === resetGeneration && revoked && !activeRevocation.has(owner)) clearOwner();
    });
  }
  function latch(resetNow: boolean): void {
    const owned = activeRevocation.get(owner) === revoke;
    revoked = true;
    if (owned) activeRevocation.delete(owner);
    if (resetNow && (owned || !activeRevocation.has(owner))) clearOwner();
    else if (!activeRevocation.has(owner)) deferOwnerReset();
  }
  function active(resetNow = false): boolean {
    if (revoked || activeRevocation.get(owner) !== revoke) return false;
    if (isCurrent()) return true;
    latch(resetNow);
    return false;
  }
  function revoke(): void {
    latch(true);
  }

  const previous = activeRevocation.get(owner);
  if (previous) previous();
  else resetInteraction.get(owner)?.();
  activeRevocation.set(owner, revoke);
  const interaction: FrequencyInteraction = {
    get inert() { return !active() || owner.inert; },
    get selectedDigitIndex() { return active() ? owner.selectedDigitIndex : null; },
    get hoveredDigitIndex() { return active() ? owner.hoveredDigitIndex : null; },
    handleWheel(digit, event) { if (active(true)) owner.handleWheel(digit, event); },
    handleDigitClick(digit, event) { if (active(true)) owner.handleDigitClick(digit, event); },
    handleKeyDown(event) { if (active(true)) owner.handleKeyDown(event); },
    handleDigitEnter(digit) { if (active(true)) owner.handleDigitEnter(digit); },
    handleDigitLeave() { if (active(true)) owner.handleDigitLeave(); },
    isSelected: (digit) => active() && owner.isSelected(digit),
    isHovered: (digit) => active() && owner.isHovered(digit),
  };
  return { interaction, get revoked() { return revoked; }, revoke };
}
