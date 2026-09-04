import { validateManagedTransmitDocument, type ManagedTransmitDocument } from '../types/managed-transmit';
const BASE = '/api/v1/managed-transmit';
export class ManagedTransmitClient {
  async snapshot(): Promise<ManagedTransmitDocument> { const response = await fetch(BASE); if (!response.ok) throw new Error(`managed transmit snapshot: ${response.status}`); return validateManagedTransmitDocument(await response.json()); }
  async command(operation: 'transmit_on' | 'force_off'): Promise<'accepted' | 'rejected'> { const response = await fetch(`${BASE}/command`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ operation }) }); if (response.status === 409) return 'rejected'; if (response.status !== 202) throw new Error(`managed transmit command: ${response.status}`); return 'accepted'; }
  async setTot(configuredSeconds: number | null): Promise<ManagedTransmitDocument> { const response = await fetch(`${BASE}/tot`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ configuredSeconds }) }); if (!response.ok) throw new Error(`managed transmit TOT: ${response.status}`); return validateManagedTransmitDocument(await response.json()); }
}
