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

/**
 * The mock log for a proposal. The mock gateway mints ids like "MOCK-P-B-1a2b3c4d" from the scenario's
 * base id, so the longest base id that prefixes the requested one is that scenario's log.
 */
export function findMockLog(proposalId: string): LogSource | undefined {
  return (
    MOCK_LOGS.find((log) => log.proposalId === proposalId) ??
    MOCK_LOGS.filter((log) => proposalId.startsWith(`${log.proposalId}-`)).sort(
      (a, b) => b.proposalId.length - a.proposalId.length,
    )[0]
  )
}

export function logPath(proposalId: string): string {
  return `/log/${encodeURIComponent(proposalId)}`
}
