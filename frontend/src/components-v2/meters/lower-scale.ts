/**
 * The second scale row `LinearSMeter` can render below its own bar
 * (MOR-2250, MOR-2509). Declared here rather than in the component so the
 * semantic hosts that BUILD a descriptor do not import the component to
 * get its type — `ReceiverInstrumentHost`'s source-pin test forbids a
 * `LinearSMeter` reference precisely to keep that boundary.
 *
 * Deliberately generic — no field may name a specific meter (no "SWR", no
 * "Po"): the caller owns what the row actually measures; this shape only
 * describes "a labeled scale row with ticks and a fill fraction".
 */
export interface LowerScaleTick {
  /** Position along the row, 0 (left edge) .. 1 (right edge) — a plain
   *  fraction, independent of any calibration domain; the caller places
   *  its own tick marks. */
  readonly value: number;
  readonly label: string;
}

export interface LowerScaleDescriptor {
  readonly label: string;
  readonly unit?: string;
  readonly stateText?: string;
  readonly accessibleDescription?: string;
  readonly ticks: readonly LowerScaleTick[];
  /** 0..1 — how much of the row's segments are lit. 0 is a legitimate
   *  "not reading right now" state (e.g. not transmitting), not "absent" —
   *  see `LinearSMeter`'s `lowerScale` prop doc for what "absent" means. */
  readonly valueFraction: number;
  readonly fault: boolean;
  /**
   * This row's OWN relevance (the field's own fact-layer `relevant`, e.g.
   * `meters.swr.relevant`) — independent of the `relevant` PROP on the
   * meter, which dims the main bar. The two drive two SIBLING `<g>`
   * groups (`data-lower-relevant`, `data-main-relevant`), neither an
   * ancestor of the other, so their opacities can never compound.
   */
  readonly relevant: boolean;
}
