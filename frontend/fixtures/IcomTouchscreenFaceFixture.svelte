<script lang="ts">
  import type { RadioViewModel, ReceiverIndicatorViewModel, TxAuxField } from '../src/semantic/radio-view-model';
  import {
    createIcomTouchscreenDisplayModel,
    type NeedleMeterDescriptor,
  } from '../src/semantic/icom-touchscreen-display-model';
  import IcomTouchscreenFace from '../src/skins/icom-touchscreen/IcomTouchscreenFace.svelte';

  const known = <T,>(value: T): TxAuxField<T> => ({
    reading: { status: 'known', value },
    availability: { structural: true, operational: true },
  });
  const unknown = <T,>(): TxAuxField<T> => ({
    reading: { status: 'unknown' },
    availability: { structural: true, operational: true },
  });

  const receiverIndicator = (
    receiver: 'MAIN' | 'SUB',
    sMeter: number,
    bandwidthHz: number,
  ): ReceiverIndicatorViewModel => ({
    receiver,
    availability: { structural: true, operational: true },
    rfState: 'receiving',
    sMeter: known(sMeter),
    bandwidthHz: known(bandwidthHz),
    agcMode: unknown(),
    nbActive: unknown(),
    nrActive: unknown(),
    notchMode: unknown(),
    attenuator: unknown(),
    preamp: unknown(),
    rfGain: unknown(),
    digiSel: unknown(),
    ipPlus: unknown(),
  });

  const view = {
    topologyId: '2/main_sub',
    vfoScheme: 'main_sub',
    activeReceiver: { status: 'known', receiver: 'MAIN' },
    vfos: [
      {
        receiver: 'MAIN', slot: { kind: 'slotted', id: 'A' }, label: 'MAIN A',
        frequencyHz: 7_070_000, mode: 'LSB', filter: 'FIL2',
        isActive: true, isActiveSlot: true, isTxTarget: false,
      },
      {
        receiver: 'SUB', slot: { kind: 'slotted', id: 'A' }, label: 'SUB A',
        frequencyHz: 14_250_000, mode: 'USB', filter: 'FIL1',
        isActive: false, isActiveSlot: true, isTxTarget: false,
      },
    ],
    split: { status: 'known', value: false },
    dualWatch: { status: 'known', value: true },
    txTarget: { status: 'unknown', reason: 'not-observed' },
    txPermit: { status: 'unknown', reason: 'tx-target-unknown' },
    scope: {
      hardwareScope: { structural: true, operational: true },
      audioFftScope: { structural: false, operational: false },
    },
    disabledReasons: [],
    receiverIndicators: [
      receiverIndicator('MAIN', -18, 2_400),
      receiverIndicator('SUB', -36, 500),
    ],
  } as RadioViewModel;

  const meterDescriptor: NeedleMeterDescriptor = {
    ticks: [
      { fraction: 0, label: '1' },
      { fraction: 0.23, label: '3' },
      { fraction: 0.46, label: '5' },
      { fraction: 0.68, label: '7' },
      { fraction: 0.8, label: '9' },
      { fraction: 0.9, label: '+20', emphasis: 'warning' },
      { fraction: 0.95, label: '+40', emphasis: 'warning' },
      { fraction: 1, label: '+60', emphasis: 'warning' },
    ],
    project: (value) => ({
      fraction: Math.max(0, Math.min(1, (value + 54) / 114)),
      label: value > 0 ? `S9+${Math.round(value)}` : `S${Math.max(0, Math.round((value + 54) / 6))}`,
    }),
  };

  const spectrum = (phase: number): Uint8Array => Uint8Array.from(
    { length: 240 },
    (_, index) => Math.round(8 + 34 * Math.exp(-(((index - phase) / 18) ** 2))
      + 24 * Math.exp(-(((index - phase * 1.7) / 9) ** 2))),
  );

  const model = createIcomTouchscreenDisplayModel({
    view,
    sMeter: meterDescriptor,
    scopeFrames: {
      MAIN: { receiver: 'MAIN', pixels: spectrum(82), startHz: 7_020_000, endHz: 7_120_000 },
      SUB: { receiver: 'SUB', pixels: spectrum(126), startHz: 14_200_000, endHz: 14_300_000 },
    },
  });
</script>

<div class="fixture-frame" data-testid="icom-touchscreen-fixture">
  <IcomTouchscreenFace {model} />
</div>

<style>
  .fixture-frame {
    width: min(96vw, 1734px);
    margin: 1rem auto;
    overflow-x: auto;
  }
</style>
