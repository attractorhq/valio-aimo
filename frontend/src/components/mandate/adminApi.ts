// Buyer-admin channel (T21): PATCH /v0.1/admin/mandates/{customerId}. Humans only; the agent view never calls it.
import type { AuditEntry, Mandate, Proposal } from '../../lib/contracts'
import { adminHeaders } from '../../lib/demoAccess'

export type MandateChange = {
  autoApproveLimitEur?: string
  hardLimitEur?: string
  allergenMode?: 'STANDARD' | 'STRICT'
  /** T68: category code prefixes; [] allows any category. */
  allowedCategories?: string[]
  /** T68: ISO dates; null clears the bound. */
  validFrom?: string | null
  validTo?: string | null
}

/** "2240, 211 ;  " → ["211", "2240"]: unique and sorted, like the gateway stores them. */
export function parseCategories(input: string): string[] {
  return [...new Set(input.split(/[\s,;]+/).map((part) => part.trim()).filter(Boolean))].sort()
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
    response = await fetch(`/v0.1/admin/mandates/${encodeURIComponent(customerId)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json', ...adminHeaders() },
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

async function adminCall<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response
  try {
    response = await fetch(`/v0.1/admin/mandates/${path}`, { ...init, headers: adminHeaders() })
  } catch {
    throw new AdminError('Could not reach the gateway.')
  }
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    throw new AdminError(data?.error?.message ?? 'The gateway refused the request.')
  }
  return data as T
}

/** T36: restore the shipped default mandate as a new, audited version; open proposals are re-decided. */
export function resetMandate(customerId: string): Promise<MandateChangeResult> {
  return adminCall(`${encodeURIComponent(customerId)}/reset`, { method: 'POST' })
}

export type MandateDifference = {
  field: string
  current: unknown
  default: unknown
}

export type MandateDefaultView = {
  mandate: Mandate
  default: Mandate
  changedFromDefault: boolean
  differences: MandateDifference[]
}

export function getMandateDefault(customerId: string): Promise<MandateDefaultView> {
  return adminCall(`${encodeURIComponent(customerId)}/default`)
}

/** "500" / "500.5" / "€510,00" → "510.00"; undefined when it is not a money amount. */
export function toMoneyString(input: string): string | undefined {
  const cleaned = input.replace(/[€\s]/g, '').replace(',', '.')
  if (!/^\d+(\.\d{1,2})?$/.test(cleaned)) return undefined
  const [whole, fraction = ''] = cleaned.split('.')
  return `${whole}.${(fraction + '00').slice(0, 2)}`
}
