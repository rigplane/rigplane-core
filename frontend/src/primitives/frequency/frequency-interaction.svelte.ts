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

  return {
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
}
