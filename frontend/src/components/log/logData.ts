// Decision-log data. Until GET /v0.1/audit is live (T08), the source is the synthetic mocks in src/mocks/.
import type { AuditTrail } from '../../lib/contracts'
import auditA from '../../mocks/audit-a.json'
import auditB from '../../mocks/audit-b.json'
import auditC from '../../mocks/audit-c.json'
import auditRecheck from '../../mocks/audit-b-recheck.json'

export type LogSource = {
  proposalId: string
  title: string
  trail: AuditTrail
}

// JSON imports widen string literals; the backend contract tests validate these files.
export const MOCK_LOGS: LogSource[] = [
  { proposalId: 'MOCK-P-A', title: 'Scenario A · Auto', trail: auditA as AuditTrail },
  { proposalId: 'MOCK-P-B', title: 'Scenario B · Ask', trail: auditB as AuditTrail },
  { proposalId: 'MOCK-P-C', title: 'Scenario C · Block', trail: auditC as AuditTrail },
  {
    proposalId: 'MOCK-P-B-RECHECK',
    title: 'Scenario B · price changed at the re-check',
    trail: auditRecheck as AuditTrail,
  },
]

export class LogNotFound extends Error {}

export async function loadAuditTrail(proposalId: string): Promise<AuditTrail> {
  const found = MOCK_LOGS.find((log) => log.proposalId === proposalId)
  if (!found) throw new LogNotFound(`No decision log for proposal ${proposalId}.`)
  return found.trail
}

export const LOG_ROOT = '/log'

export function logPath(proposalId: string): string {
  return `${LOG_ROOT}/${encodeURIComponent(proposalId)}`
}

/** "/log/MOCK-P-B" → "MOCK-P-B"; "/log" → undefined. */
export function proposalIdFromPath(pathname: string): string | undefined {
  const match = pathname.match(/^\/log\/([^/]+)\/?$/)
  return match ? decodeURIComponent(match[1]) : undefined
}
