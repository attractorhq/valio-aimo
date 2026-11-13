// Buyer-admin channel (T21): PATCH /v0.1/admin/mandates/{customerId}. Humans only; the agent view never calls it.
import { API_BASE } from '../../lib/api'
import type { AuditEntry, Mandate, Proposal } from '../../lib/contracts'

export type MandateChange = {
  autoApproveLimitEur?: string
  hardLimitEur?: string
  allergenMode?: 'STANDARD' | 'STRICT'
}

export type MandateChangeResult = {
  mandate: Mandate
  auditEntry: AuditEntry
  superseded: string[]
  redecided: Proposal[]
}

export class AdminError extends Error {}

export async function changeMandate(
  customerId: string,
  change: MandateChange,
): Promise<MandateChangeResult> {
  let response: Response
  try {
    response = await fetch(`${API_BASE}/v0.1/admin/mandates/${encodeURIComponent(customerId)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(change),
    })
  } catch {
    throw new AdminError('Could not reach the gateway.')
  }
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    throw new AdminError(data?.error?.message ?? 'The gateway refused the change.')
  }
  return data as MandateChangeResult
}

/** "500" / "500.5" / "€510,00" → "510.00"; undefined when it is not a money amount. */
export function toMoneyString(input: string): string | undefined {
  const cleaned = input.replace(/[€\s]/g, '').replace(',', '.')
  if (!/^\d+(\.\d{1,2})?$/.test(cleaned)) return undefined
  const [whole, fraction = ''] = cleaned.split('.')
  return `${whole}.${(fraction + '00').slice(0, 2)}`
}
