import type {
  MeterField, RadioViewModel, ReceiverId, ReceiverIndicatorViewModel, TxAuxField,
  VfoViewModel,
} from './radio-view-model';

export type DisplayValue<T> =
  | { readonly state: 'known'; readonly value: T }
  | { readonly state: 'unknown' }
  | { readonly state: 'unsupported' };

export type DisplayIndicator =
  | { readonly state: 'active' }
  | { readonly state: 'inactive' }
  | { readonly state: 'unknown' }
  | { readonly state: 'unsupported' };

export type DisplayOffset =
  | { readonly state: 'active'; readonly offsetHz: number }
  | { readonly state: 'inactive'; readonly offsetHz?: number }
  | { readonly state: 'unknown' | 'unsupported' };

export type DisplayTelemetry = DisplayValue<number> & { readonly relevant: boolean };

export interface PeerSplitReceiverDisplay {
  readonly receiver: ReceiverId;
  readonly label: string;
  readonly activity: 'active' | 'inactive' | 'unknown';
  readonly operational: boolean;
  readonly frequency: DisplayValue<number>;
  readonly mode: DisplayValue<string>;
  readonly filter: DisplayValue<string>;
  readonly band: DisplayValue<string>;
  readonly sMeter: DisplayValue<number>;
  readonly bandwidthHz: DisplayValue<number>;
  readonly ifShiftHz: DisplayValue<number>;
  readonly pbtInnerHz: DisplayValue<number>;
  readonly pbtOuterHz: DisplayValue<number>;
  readonly spectrum: 'inactive' | 'waiting' | 'unsupported' | 'unknown';
  readonly dsp: {
    readonly agc: DisplayValue<number | string>;
    readonly nb: DisplayIndicator;
    readonly nr: DisplayIndicator;
    readonly notch: DisplayValue<'off' | 'auto' | 'manual'>;
  };
  readonly front: {
    readonly preamp: DisplayValue<number>;
    readonly attenuator: DisplayValue<number>;
    readonly rfGain: DisplayValue<number>;
    readonly digiSel: DisplayIndicator;
    readonly ipPlus: DisplayIndicator;
  };
}

export interface PeerSplitDisplayModel {
  readonly kind: 'peer-split';
  readonly rfState: 'receiving' | 'transmitting' | 'uncertain' | 'unknown';
  readonly receivers: readonly [PeerSplitReceiverDisplay, PeerSplitReceiverDisplay];
  readonly activeReceiver: PeerSplitReceiverDisplay | null;
  readonly top: {
    readonly vox: DisplayIndicator;
    readonly compressor: DisplayIndicator;
    readonly split: DisplayIndicator;
    readonly rit: DisplayIndicator;
    readonly tx: DisplayIndicator;
    readonly tune: DisplayIndicator;
    readonly atu: DisplayIndicator;
    readonly antenna: DisplayValue<number>;
  };
  readonly offsets: {
    readonly rit: DisplayOffset;
    readonly xit: DisplayOffset;
    readonly split: DisplayOffset;
  };
  readonly telemetry: {
    readonly drainVoltage: DisplayTelemetry;
    readonly drainCurrent: DisplayTelemetry;
    readonly power: DisplayTelemetry;
    readonly swr: DisplayTelemetry;
    readonly alc: DisplayTelemetry;
    readonly compression: DisplayTelemetry;
  };
}

type Fact<T> = Pick<TxAuxField<T>, 'reading' | 'availability'>;

function displayValue<T>(field: Fact<T> | undefined): DisplayValue<T> {
  if (!field || !field.availability.structural) return { state: 'unsupported' };
  if (!field.availability.operational || field.reading.status === 'unknown') return { state: 'unknown' };
  return { state: 'known', value: field.reading.value };
}

function directValue<T>(value: T | null | undefined): DisplayValue<T> {
  return value === null || value === undefined ? { state: 'unknown' } : { state: 'known', value };
}

function displayIndicator(field: Fact<boolean> | undefined): DisplayIndicator {
  const value = displayValue(field);
  if (value.state !== 'known') return value;
  return { state: value.value ? 'active' : 'inactive' };
}

function booleanIndicator(value: RadioViewModel['split']): DisplayIndicator {
  return value.status === 'known'
    ? { state: value.value ? 'active' : 'inactive' }
    : { state: 'unknown' };
}

function selectedVfo(view: RadioViewModel, receiver: ReceiverId): VfoViewModel | undefined {
  const receiverVfos = view.vfos.filter((vfo) => vfo.receiver === receiver);
  return receiverVfos.find((vfo) => vfo.isActiveSlot) ?? receiverVfos[0];
}

function receiverIndicator(
  view: RadioViewModel, receiver: ReceiverId,
): ReceiverIndicatorViewModel | undefined {
  return view.receiverIndicators?.find((indicator) => indicator.receiver === receiver);
}

function receiverDisplay(view: RadioViewModel, receiver: ReceiverId): PeerSplitReceiverDisplay {
  const vfo = selectedVfo(view, receiver);
  const indicator = receiverIndicator(view, receiver);
  const activity = view.activeReceiver.status === 'unknown'
    ? 'unknown'
    : view.activeReceiver.receiver === receiver ? 'active' : 'inactive';
  const isActive = activity === 'active';
  const scopeStructural = view.scope.audioFftScope.structural;
  const spectrum = !scopeStructural ? 'unsupported'
    : activity === 'unknown' ? 'unknown'
      : isActive ? 'waiting' : 'inactive';

  return {
    receiver,
    label: view.vfoScheme === 'ab_shared'
      ? (receiver === 'MAIN' ? 'VFO A' : 'VFO B')
      : vfo?.label || receiver,
    activity,
    operational: indicator?.availability.operational ?? false,
    frequency: directValue(vfo?.frequencyHz),
    mode: directValue(vfo?.mode),
    filter: directValue(vfo?.filter),
    band: isActive ? displayValue(view.band?.currentBand) : { state: 'unsupported' },
    sMeter: displayValue(indicator?.sMeter),
    bandwidthHz: displayValue(indicator?.bandwidthHz),
    ifShiftHz: isActive ? displayValue(view.filterPassband?.ifShift) : { state: 'unsupported' },
    pbtInnerHz: isActive ? displayValue(view.filterPassband?.pbtInner) : { state: 'unsupported' },
    pbtOuterHz: isActive ? displayValue(view.filterPassband?.pbtOuter) : { state: 'unsupported' },
    spectrum,
    dsp: {
      agc: displayValue(indicator?.agcMode),
      nb: displayIndicator(indicator?.nbActive),
      nr: displayIndicator(indicator?.nrActive),
      notch: displayValue(indicator?.notchMode),
    },
    front: {
      preamp: displayValue(indicator?.preamp),
      attenuator: displayValue(indicator?.attenuator),
      rfGain: displayValue(indicator?.rfGain),
      digiSel: displayIndicator(indicator?.digiSel),
      ipPlus: displayIndicator(indicator?.ipPlus),
    },
  };
}

function offset(
  active: Fact<boolean> | undefined, value: Fact<number> | undefined,
): DisplayOffset {
  const activeValue = displayValue(active);
  const offsetValue = displayValue(value);
  if (activeValue.state === 'unsupported' && offsetValue.state === 'unsupported') {
    return { state: 'unsupported' };
  }
  if (activeValue.state !== 'known' || offsetValue.state !== 'known') return { state: 'unknown' };
  return { state: activeValue.value ? 'active' : 'inactive', offsetHz: offsetValue.value };
}

function splitOffset(
  view: RadioViewModel, active: PeerSplitReceiverDisplay | null,
): DisplayOffset {
  if (!active) return { state: 'unknown' };
  if (view.split.status === 'unknown') return { state: 'unknown' };
  if (!view.split.value) return { state: 'inactive' };
  if (view.txTarget.status === 'unknown'
    || view.txTarget.frequencyHz === null
    || active.frequency.state !== 'known') return { state: 'unknown' };
  return { state: 'active', offsetHz: view.txTarget.frequencyHz - active.frequency.value };
}

function atuIndicator(
  field: TxAuxField<'off' | 'on' | 'tuning'> | undefined, mode: 'atu' | 'tune',
): DisplayIndicator {
  const value = displayValue(field);
  if (value.state !== 'known') return value;
  return { state: mode === 'tune'
    ? (value.value === 'tuning' ? 'active' : 'inactive')
    : (value.value === 'off' ? 'inactive' : 'active') };
}

function telemetry(field: MeterField | undefined): DisplayTelemetry {
  const value = displayValue(field);
  return { ...value, relevant: field?.relevant ?? false };
}

export function projectPeerSplitDisplay(view: RadioViewModel): PeerSplitDisplayModel {
  const main = receiverDisplay(view, 'MAIN');
  const sub = receiverDisplay(view, 'SUB');
  const receivers = [main, sub] as const;
  const activeReceiver = receivers.find((receiver) => receiver.activity === 'active') ?? null;
  const shared = view.radioWideIndicators;
  const rfState = shared?.rfState ?? view.meters?.rfState ?? 'unknown';
  const atu = shared?.atu ?? view.txAux?.atu;

  return {
    kind: 'peer-split',
    rfState,
    receivers,
    activeReceiver,
    top: {
      vox: displayIndicator(view.txAux?.vox),
      compressor: displayIndicator(view.txAux?.compressor),
      split: booleanIndicator(view.split),
      rit: displayIndicator(shared?.ritActive),
      tx: rfState === 'transmitting' ? { state: 'active' }
        : rfState === 'receiving' ? { state: 'inactive' } : { state: 'unknown' },
      tune: atuIndicator(atu, 'tune'),
      atu: atuIndicator(atu, 'atu'),
      antenna: displayValue(shared?.antenna),
    },
    offsets: {
      rit: offset(shared?.ritActive, shared?.ritOffset),
      xit: offset(shared?.xitActive, shared?.xitOffset),
      split: splitOffset(view, activeReceiver),
    },
    telemetry: {
      drainVoltage: telemetry(view.meters?.drainVoltage),
      drainCurrent: telemetry(view.meters?.drainCurrent),
      power: telemetry(view.meters?.power),
      swr: telemetry(view.meters?.swr),
      alc: telemetry(view.meters?.alc),
      compression: telemetry(view.meters?.compression),
    },
  };
}
