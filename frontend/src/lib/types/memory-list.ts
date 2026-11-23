/** `GET /api/v1/memories` (MOR-3627): the radio's stored memory channels, read-only. */

type ToneModeDto = 'off' | 'tone' | 'tsql' | 'dtcs' | 'pr_freq' | 'rev_tone';

type DuplexDto = 'simplex' | 'plus' | 'minus';

/**
 * The second (TX/split) channel block of a stored entry (MOR-3648): what a
 * read-modify-write must not silently lose when the write API cannot set it.
 */
export interface MemoryBlockDto {
  frequencyHz: number;
  mode: string;
  filter: number;
  dataMode: boolean;
  duplex: DuplexDto | null;
  offsetHz: number;
  toneMode: ToneModeDto | null;
  toneCentiHz: number;
  tsqlCentiHz: number;
  dtcsCode: number;
  dtcsPolarity: number;
}

export interface MemoryEntryDto {
  group: number;
  channel: number;
  empty: boolean;
  name?: string;
  frequencyHz?: number;
  mode?: string;
  dataMode?: boolean;
  duplex?: DuplexDto | null;
  offsetHz?: number | null;
  toneMode?: ToneModeDto | null;
  toneCentiHz?: number | null;
  tsqlCentiHz?: number | null;
  dtcsCode?: number | null;
  // MOR-3648: the fields a read-modify-write must not silently lose.
  filter?: number | null;
  dtcsPolarity?: number | null;
  split?: boolean;
  txBlock?: MemoryBlockDto | null;
}

export interface MemoryPageDto {
  addressing: 'group_channel' | 'band_channel' | 'channel';
  group: number;
  groups: { first: number; last: number };
  channels: { first: number; last: number };
  recall: boolean;
  entries: MemoryEntryDto[];
}
