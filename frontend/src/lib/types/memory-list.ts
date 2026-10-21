/** `GET /api/v1/memories` (MOR-3627): the radio's stored memory channels, read-only. */
export interface MemoryEntryDto {
  group: number;
  channel: number;
  empty: boolean;
  name?: string;
  frequencyHz?: number;
  mode?: string;
  dataMode?: boolean;
  duplex?: 'simplex' | 'plus' | 'minus' | null;
  offsetHz?: number;
  toneMode?: 'off' | 'tone' | 'tsql' | 'dtcs' | null;
  toneCentiHz?: number;
  tsqlCentiHz?: number;
  dtcsCode?: number;
}

export interface MemoryPageDto {
  addressing: 'group_channel' | 'band_channel';
  group: number;
  groups: { first: number; last: number };
  channels: { first: number; last: number };
  recall: boolean;
  entries: MemoryEntryDto[];
}
