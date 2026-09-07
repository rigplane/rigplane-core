import type {
  FiniteRendererContext, FiniteRendererSeat,
} from '../control-instruments/control-instrument-renderer.svelte';

export type FrequencyEntryValidation = 'empty' | 'accepted' | 'rejected' | 'unavailable';

export interface FrequencyEntryRendererView {
  readonly label: string;
  readonly draft: string;
  readonly validation: FrequencyEntryValidation;
  readonly boundsAvailable: boolean;
  readonly interpretedHz: number | null;
  readonly inputAvailable: boolean;
  readonly submitAvailable: boolean;
  readonly hint: string;
  readonly rangeText: string;
  readonly unavailableReason?: string;
}

export interface FrequencyEntryRendererLease {
  readonly active: boolean;
  readonly view: Readonly<FrequencyEntryRendererView> | undefined;
  setDraft(raw: string): void;
  submit(): void;
  cancel(): void;
  handleKeyDown(event: KeyboardEvent): void;
  dispose(): void;
}

export interface FrequencyEntryRendererProps {
  readonly lease: FrequencyEntryRendererLease;
}

export interface FrequencyEntryRendererInput {
  readonly context: FiniteRendererContext | null;
  readonly view: Readonly<FrequencyEntryRendererView>;
  setDraft(raw: string): void;
  submit(): void;
  cancel(): void;
  handleKeyDown(event: KeyboardEvent): void;
}

export type FrequencyEntryRendererSeat = FiniteRendererSeat<FrequencyEntryRendererLease>;

export function createFrequencyEntryRendererSeat(
  readCurrent: () => FrequencyEntryRendererInput,
): FrequencyEntryRendererSeat {
  let destroyed = false;
  const revokers = new Set<() => void>();
  return {
    attachRenderer() {
      const captured = readCurrent().context;
      let revoked = captured === null;
      const revoke = () => {
        if (revoked) return;
        revoked = true;
        revokers.delete(revoke);
      };
      if (!revoked) revokers.add(revoke);
      const current = () => {
        if (destroyed || revoked) return undefined;
        const next = readCurrent();
        if (next.context === null || !Object.is(next.context, captured)) {
          revoke();
          return undefined;
        }
        return next;
      };
      return {
        get active() { return current() !== undefined; },
        get view() { return current()?.view; },
        setDraft(raw) { current()?.setDraft(raw); },
        submit() { current()?.submit(); },
        cancel() { current()?.cancel(); },
        handleKeyDown(event) { current()?.handleKeyDown(event); },
        dispose: revoke,
      };
    },
    destroy() {
      if (destroyed) return;
      destroyed = true;
      for (const revoke of [...revokers]) revoke();
    },
  };
}
