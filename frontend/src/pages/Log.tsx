import { useQuery } from '@tanstack/react-query'
import { ArrowLeft, ScrollText } from 'lucide-react'
import { DecisionLog } from '../components/log/DecisionLog'
import { MOCK_LOGS, loadAuditTrail, logPath } from '../components/log/logData'

/** Decision log per proposal (P6 P0-5): readable for the buyer and Valio Aimo without knowing the code. */
export function Log({ proposalId }: { proposalId?: string }) {
  const trail = useQuery({
    queryKey: ['audit', proposalId],
    queryFn: () => loadAuditTrail(proposalId!),
    enabled: Boolean(proposalId),
    retry: false,
  })
  const current = MOCK_LOGS.find((log) => log.proposalId === proposalId)

  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <a
          href="/"
          className="inline-flex items-center gap-1 font-semibold text-[var(--green)] underline"
        >
          <ArrowLeft size={16} aria-hidden /> Back to the purchasing flow
        </a>
        <span className="mode-badge mode-mock">Synthetic mock data</span>
      </div>

      <section className="panel" aria-labelledby="log-title">
        <h2 id="log-title" className="panel-title inline-flex items-center gap-2">
          <ScrollText size={18} aria-hidden /> Decision log
          {proposalId && (
            <span className="font-mono text-sm font-normal">· proposal {proposalId}</span>
          )}
        </h2>
        <p className="muted small">
          Every step of the order: what happened, who or what decided (the agent, a gateway rule or
          a named human), which rule applied and which facts it used, with their trust labels.
        </p>

        <nav aria-label="Proposals with a decision log" className="mt-3">
          <ul className="flex flex-wrap gap-2">
            {MOCK_LOGS.map((log) => (
              <li key={log.proposalId}>
                <a
                  href={logPath(log.proposalId)}
                  aria-current={log.proposalId === proposalId ? 'page' : undefined}
                  className={`btn ${log.proposalId === proposalId ? 'btn-primary' : 'btn-scenario'}`}
                >
                  {log.title}
                </a>
              </li>
            ))}
          </ul>
        </nav>
      </section>

      {!proposalId && <p className="notice">Pick a proposal to read its decision log.</p>}
      {trail.error && (
        <p className="notice notice-error" role="alert">
          {trail.error.message}
        </p>
      )}
      {trail.data && (
        <section aria-label={current ? current.title : `Proposal ${proposalId}`}>
          <DecisionLog entries={trail.data.entries} />
        </section>
      )}
    </div>
  )
}
