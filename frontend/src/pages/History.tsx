import { useQuery } from '@tanstack/react-query'
import { CheckCheck, FileCheck2, Search, ShieldCheck } from 'lucide-react'
import { useSearchParams } from 'react-router-dom'
import { readableTime, request, type Identity } from '../lib/api'
import type { AuditEvent, Proposal } from '../lib/types'
import { EmptyState, ErrorNotice, Loading } from '../components/Common'
import { ProposalCard } from '../components/ProposalCard'

export function History({ identity }: { identity: Identity }) {
  const [params, setParams] = useSearchParams()
  const selected = params.get('proposal') || ''
  const proposals = useQuery({
    queryKey: ['proposals', identity],
    queryFn: () => request<Proposal[]>(identity, '/api/v1/proposals'),
  })
  const events = useQuery({
    queryKey: ['audit', identity, selected],
    queryFn: () =>
      request<AuditEvent[]>(
        identity,
        `/api/v1/audit${selected ? `?proposal_id=${encodeURIComponent(selected)}` : ''}`,
      ),
    refetchInterval: 10_000,
  })
  const selectedProposal = proposals.data?.find((p) => p.id === selected)
  return (
    <>
      <div className="page-heading">
        <span className="eyebrow">EVERY DECISION, ACCOUNTED FOR</span>
        <h1>Nothing behind the curtain.</h1>
        <p>
          Follow the evidence from request to commitment. Facts and actions, never hidden model
          reasoning.
        </p>
      </div>
      <div className="history-filter">
        <label>
          Purchase timeline
          <select
            value={selected}
            onChange={(e) => setParams(e.target.value ? { proposal: e.target.value } : {})}
          >
            <option value="">All recent activity</option>
            {proposals.data?.map((p) => (
              <option value={p.id} key={p.id}>
                {p.buyer_name} · {p.id.slice(0, 8)} · {p.status.replaceAll('_', ' ')}
              </option>
            ))}
          </select>
        </label>
        <span className="muted small">Times in Europe/Helsinki · latest 250 events</span>
      </div>
      <ErrorNotice error={events.error || proposals.error} />
      {selectedProposal && (
        <ProposalCard
          proposal={selectedProposal}
          identity={identity}
          onUpdate={() => {
            void proposals.refetch()
            void events.refetch()
          }}
        />
      )}
      {events.isPending && <Loading />}
      {!events.isPending && !events.data?.length && (
        <EmptyState title="Your decision trail starts here">
          Search for a product or create your first purchase proposal. The gateway records its
          checks as you go.
        </EmptyState>
      )}
      <div className="timeline">
        {events.data?.map((event) => {
          const Icon = event.event.includes('SIMULATED')
            ? CheckCheck
            : event.event.includes('POLICY') || event.event.includes('HUMAN')
              ? ShieldCheck
              : event.event.includes('SEARCH')
                ? Search
                : FileCheck2
          return (
            <article className="timeline-item" key={event.id}>
              <div
                className={`timeline-icon ${event.event.includes('BLOCKED') || event.event.includes('REJECTED') ? 'blocked' : ''}`}
              >
                <Icon size={18} />
              </div>
              <div className="timeline-content">
                <div className="section-heading">
                  <span className="event-label">{event.event.replaceAll('_', ' ')}</span>
                  <time>{readableTime(event.created_at)}</time>
                </div>
                <h3>{event.summary}</h3>
                <div className="meta-row">
                  <span>
                    {event.actor} · {event.buyer_id}
                  </span>
                  <span>
                    {event.proposal_id
                      ? `Proposal #${event.proposal_id.slice(0, 8)}`
                      : 'Read-only inquiry'}
                  </span>
                </div>
                <details>
                  <summary>View recorded evidence</summary>
                  <pre>{JSON.stringify(event.data, null, 2)}</pre>
                </details>
              </div>
            </article>
          )
        })}
      </div>
    </>
  )
}
