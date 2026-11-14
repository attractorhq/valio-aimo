import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ShieldAlert, ShieldCheck } from 'lucide-react'
import type { Proposal } from '../../lib/contracts'
import { approverChannel, gateway, listBuyerProposals } from '../../lib/gateway'
import { adminHeaders, agentHeaders } from '../../lib/demoAccess'
import { formatClock, formatMoney } from '../../lib/format'
import { LoadingSection } from '../Loading'
import { logPath } from './logData'

const STATE_WORD: Record<string, string> = {
  PENDING_APPROVAL: 'Waiting',
  APPROVED: 'Approved',
  AUTO_APPROVED: 'Approved',
  RECHECKED: 'Re-checked',
  CONFIRMED_SIMULATED: 'Confirmed',
  REJECTED: 'Rejected',
  EXPIRED: 'Expired',
  SUPERSEDED: 'Superseded',
  BLOCKED: 'Blocked',
}

function itemsSummary(proposal: Proposal): string {
  return proposal.request.items.map((i) => `${i.quantity} ${i.unit} ${i.what}`).join(', ')
}

type ChainStatus = { verified: boolean; chainLength: number }

async function verifyChain(customerId?: string): Promise<ChainStatus | null> {
  if (gateway.source !== 'GATEWAY') return null
  const query = customerId ? `?customerId=${encodeURIComponent(customerId)}` : ''
  const res = await fetch(`/v0.1/audit/verify${query}`, {
    headers: { Accept: 'application/json', ...(customerId ? agentHeaders(customerId) : adminHeaders()) },
  })
  return res.ok ? ((await res.json()) as ChainStatus) : null
}

/** T80: `/log` without a proposal lists recent proposals per buyer; each opens its decision log. */
export function ProposalList() {
  const [buyer, setBuyer] = useState('')
  const mandates = useQuery({ queryKey: ['mandates'], queryFn: () => gateway.listMandates() })
  useEffect(() => {
    if (!buyer && mandates.data?.length) setBuyer(mandates.data[0].customerId)
  }, [buyer, mandates.data])
  const proposals = useQuery({
    queryKey: ['log-proposals', buyer],
    queryFn: () => buyer === 'ALL' ? approverChannel.listAllProposals() : listBuyerProposals(buyer),
    enabled: Boolean(buyer),
    refetchInterval: 5000,
  })
  const chain = useQuery({
    queryKey: ['log-chain', buyer, gateway.source],
    queryFn: () => verifyChain(buyer === 'ALL' ? undefined : buyer),
    // The data source is known once the list has loaded (mock mode has no server-side chain).
    enabled: proposals.isSuccess,
    retry: false,
  })
  const names = new Map((mandates.data ?? []).map((m) => [m.customerId, m.organisationName]))
  const all = (proposals.data ?? []).map((i) => i.proposal)
  const shown = buyer === 'ALL' ? all : all.filter((p) => p.customerId === buyer)
  const groups = [...new Set(shown.map((p) => p.customerId))]

  return (
    <section className="panel" aria-labelledby="log-list-title">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 id="log-list-title" className="panel-title mb-0">
          Recent proposals
        </h3>
        <div className="flex flex-wrap items-center gap-2">
          {chain.data && (
            <span
              className={`status-pill ${chain.data.verified ? 'status-done' : 'status-blocked'}`}
              role="status"
              aria-label="Hash-chain verification status"
            >
              {chain.data.verified ? <ShieldCheck size={13} aria-hidden /> : <ShieldAlert size={13} aria-hidden />}
              <span>{chain.data.verified ? 'Hash chain verified ✓' : 'Hash chain broken'}</span>
            </span>
          )}
          <label className="inline-flex items-center gap-1.5 text-xs font-semibold">
            <span>Buyer</span>
            <select
              value={buyer}
              onChange={(e) => setBuyer(e.target.value)}
              aria-label="Filter proposals by buyer"
              className="rounded-[12px] border border-[var(--line-strong)] bg-[var(--surface)] px-2 py-1 text-xs"
            >
              <option value="ALL" disabled={!adminHeaders()['X-Demo-Admin-Key']}>All buyers (admin key required)</option>
              {(mandates.data ?? []).map((m) => (
                <option key={m.customerId} value={m.customerId}>
                  {m.organisationName}
                </option>
              ))}
            </select>
          </label>
        </div>
      </div>

      {proposals.isPending && <LoadingSection label="proposals" />}
      {proposals.error && (
        <p className="notice notice-error mt-2" role="alert">
          {proposals.error.message}
        </p>
      )}
      {proposals.data && shown.length === 0 && (
        <p className="muted small mt-2">No proposals yet. Run a request to create one.</p>
      )}
      {groups.map((customerId) => (
        <div key={customerId} className="mt-3">
          <h4 className="text-xs font-bold uppercase tracking-wider text-[var(--muted)]">
            {names.get(customerId) ?? customerId}
          </h4>
          <div className="table-scroll">
            <table className="data-table text-sm" aria-label={`Proposals for ${names.get(customerId) ?? customerId}`}>
              <thead>
                <tr>
                  <th scope="col">Time</th>
                  <th scope="col">Items</th>
                  <th scope="col" className="num">
                    Total
                  </th>
                  <th scope="col">Decision</th>
                  <th scope="col">State</th>
                </tr>
              </thead>
              <tbody>
                {shown
                  .filter((p) => p.customerId === customerId)
                  .map((p) => (
                    <tr key={p.proposalId}>
                      <td>
                        <a href={logPath(p.proposalId)} className="font-semibold text-[var(--green)] underline">
                          {formatClock(p.createdAt)}
                        </a>
                      </td>
                      <td>{itemsSummary(p)}</td>
                      <td className="num">{formatMoney(p.totals.exclVat)}</td>
                      <td>
                        <span className="chip">{p.decision}</span>
                      </td>
                      <td>{STATE_WORD[p.state] ?? p.state}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        </div>
      ))}
    </section>
  )
}
