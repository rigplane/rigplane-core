import type {
  Availability,
  RadioViewModel,
  ReceiverId,
  ReceiverIndicatorViewModel,
  TxAuxField,
  VfoViewModel,
} from './radio-view-model';

export type DisplayFact<T> =
  | { readonly status: 'known'; readonly value: T }
  | { readonly status: 'unknown'; readonly value?: undefined }
  | { readonly status: 'inactive'; readonly value?: T }
  | { readonly status: 'unsupported'; readonly value?: undefined };

export interface NeedleMeterTick {
  readonly fraction: number;
  readonly label: string;
  readonly emphasis?: 'normal' | 'warning';
}

export interface NeedleMeterReading {
  readonly fraction: number;
  readonly label: string;
}

/**
 * Pure seam supplied by the capability-owning layer. It deliberately carries
 * no calibration table of its own and never reads the capabilities store.
 */
export interface NeedleMeterDescriptor {
  readonly ticks: readonly NeedleMeterTick[];
  readonly project: (calibratedValue: number) => NeedleMeterReading | null;
}

/** App-owned pixels. A face may display this value but must not subscribe for it. */
export interface ReceiverScopeFrame {
  readonly receiver: ReceiverId;
  readonly pixels: Uint8Array;
  readonly startHz?: number;
  readonly endHz?: number;
}

export interface ReceiverScopeDisplay {
  readonly pixels: Uint8Array;
  readonly startHz?: number;
  readonly endHz?: number;
}

export interface ReceiverInstrumentDisplayModel {
  readonly receiver: ReceiverId;
  readonly receiverActivity: 'active' | 'inactive' | 'unknown';
  readonly frequencyHz: DisplayFact<number>;
  readonly mode: DisplayFact<string>;
  readonly filter: DisplayFact<string>;
  readonly bandwidthHz: DisplayFact<number>;
  readonly sMeter: DisplayFact<NeedleMeterReading>;
  readonly sMeterTicks: readonly NeedleMeterTick[];
  readonly scope: DisplayFact<ReceiverScopeDisplay>;
}

export interface IcomTouchscreenDisplayModel {
  readonly receivers: readonly [ReceiverInstrumentDisplayModel, ReceiverInstrumentDisplayModel];
}

export interface IcomTouchscreenDisplayInput {
  readonly view: RadioViewModel;
  readonly sMeter: NeedleMeterDescriptor | null;
  readonly scopeFrames?: Readonly<Partial<Record<ReceiverId, ReceiverScopeFrame | null>>>;
}

const RECEIVERS: readonly [ReceiverId, ReceiverId] = ['MAIN', 'SUB'];

function validFinite(value: number): boolean {
  return Number.isFinite(value);
}

function fieldState<T>(field: TxAuxField<T> | undefined): DisplayFact<T> {
  if (!field) return { status: 'unknown' };
  if (!field.availability.structural) return { status: 'unsupported' };
  if (!field.availability.operational) {
    return field.reading.status === 'known'
      ? { status: 'inactive', value: field.reading.value }
      : { status: 'inactive' };
  }
  return field.reading.status === 'known'
    ? { status: 'known', value: field.reading.value }
    : { status: 'unknown' };
}

function nullableState<T>(value: T | null | undefined): DisplayFact<T> {
  return value === null || value === undefined
    ? { status: 'unknown' }
    : { status: 'known', value };
}

function activeVfo(view: RadioViewModel, receiver: ReceiverId): VfoViewModel | null {
  const candidates = view.vfos.filter((vfo) => vfo.receiver === receiver && vfo.isActiveSlot);
  return candidates.length === 1 ? candidates[0] : null;
}

function indicatorFor(
  view: RadioViewModel,
  receiver: ReceiverId,
): ReceiverIndicatorViewModel | undefined {
  return view.receiverIndicators?.find((indicator) => indicator.receiver === receiver);
}

function activity(view: RadioViewModel, receiver: ReceiverId): ReceiverInstrumentDisplayModel['receiverActivity'] {
  if (view.activeReceiver.status === 'unknown') return 'unknown';
  return view.activeReceiver.receiver === receiver ? 'active' : 'inactive';
}

function meterState(
  indicator: ReceiverIndicatorViewModel | undefined,
  descriptor: NeedleMeterDescriptor | null,
): DisplayFact<NeedleMeterReading> {
  const source = fieldState(indicator?.sMeter);
  if (source.status === 'unsupported' || source.status === 'unknown') return source;
  if (source.value === undefined) return { status: 'inactive' };
  if (!descriptor || !validFinite(source.value)) {
    return source.status === 'inactive' ? { status: 'inactive' } : { status: 'unknown' };
  }
  const projected = descriptor.project(source.value);
  if (
    !projected
    || !validFinite(projected.fraction)
    || projected.fraction < 0
    || projected.fraction > 1
    || projected.label.trim() === ''
  ) {
    return source.status === 'inactive' ? { status: 'inactive' } : { status: 'unknown' };
  }
  return source.status === 'inactive'
    ? { status: 'inactive', value: projected }
    : { status: 'known', value: projected };
}

function scopeState(
  availability: Availability,
  receiver: ReceiverId,
  frame: ReceiverScopeFrame | null | undefined,
): DisplayFact<ReceiverScopeDisplay> {
  if (!availability.structural) return { status: 'unsupported' };
  if (!availability.operational) return { status: 'inactive' };
  if (!frame || frame.receiver !== receiver || frame.pixels.length === 0) return { status: 'unknown' };

  const startHz = validFinite(frame.startHz ?? Number.NaN) ? frame.startHz : undefined;
  const endHz = validFinite(frame.endHz ?? Number.NaN) ? frame.endHz : undefined;
  const boundsValid = startHz !== undefined && endHz !== undefined && endHz > startHz;
  return {
    status: 'known',
    value: {
      pixels: new Uint8Array(frame.pixels),
      ...(boundsValid ? { startHz, endHz } : {}),
    },
  };
}

function receiverModel(
  input: IcomTouchscreenDisplayInput,
  receiver: ReceiverId,
): ReceiverInstrumentDisplayModel {
  const vfo = activeVfo(input.view, receiver);
  const indicator = indicatorFor(input.view, receiver);
  return {
    receiver,
    receiverActivity: activity(input.view, receiver),
    frequencyHz: nullableState(vfo?.frequencyHz),
    mode: nullableState(vfo?.mode),
    filter: nullableState(vfo?.filter),
    bandwidthHz: fieldState(indicator?.bandwidthHz),
    sMeter: meterState(indicator, input.sMeter),
    sMeterTicks: input.sMeter?.ticks ?? [],
    scope: scopeState(input.view.scope.hardwareScope, receiver, input.scopeFrames?.[receiver]),
  };
}

/** Project the canonical semantic model into two independent passive clusters. */
export function createIcomTouchscreenDisplayModel(
  input: IcomTouchscreenDisplayInput,
): IcomTouchscreenDisplayModel {
  return {
    receivers: [receiverModel(input, RECEIVERS[0]), receiverModel(input, RECEIVERS[1])],
  };
}
