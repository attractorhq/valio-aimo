import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Check, ChevronRight, UserRoundCheck } from 'lucide-react'
import { approverChannel, gateway, type ApproverItem } from '../lib/gateway'
import { formatClock, formatMoney, reasonChip } from '../lib/format'
import { ApprovalCard } from '../components/approver/ApprovalCard'
import {
  approvalReasons,
  contextReasons,
  productSummary,
  rowStatement,
} from '../components/approver/explain'

const STATE_WORD: Record<string, string> = {
  PENDING_APPROVAL: 'Waiting',
  CONFIRMED_SIMULATED: 'Approved',
  REJECTED: 'Rejected',
  EXPIRED: 'Expired',
  SUPERSEDED: 'Superseded',
}

/**
 * The Approver panel: the human approval channel, visibly not the agent (P3, P10).
 * It is the only screen that holds an approval capability; the agent view never does.
 */
export function Approver() {
  const mandates = useQuery({ queryKey: ['mandates'], queryFn: () => gateway.listMandates() })
  const [customerId, setCustomerId] = useState<string>()
  const [selectedId, setSelectedId] = useState<string>()

  // Default to the school kitchen's Head Chef, the approver in the main demo.
  useEffect(() => {
    if (!customerId && mandates.data?.length) {
      const strict = mandates.data.find((m) => m.allergenMode === 'STRICT')
      setCustomerId((strict ?? mandates.data[0]).customerId)
    }
  }, [customerId, mandates.data])

  const mandate = mandates.data?.find((m) => m.customerId === customerId)
  const list = useQuery({
    queryKey: ['approver-proposals', customerId],
    queryFn: () => approverChannel.listProposals(customerId!),
    enabled: Boolean(customerId),
    refetchInterval: 2000,
  })
  const items = list.data ?? []
  const pending = items.filter((i) => i.proposal.state === 'PENDING_APPROVAL')
  const selected: ApproverItem | undefined =
    items.find((i) => i.proposal.proposalId === selectedId) ?? pending[0] ?? items[0]

  return (
    <div className="approver-page">
      <header className="approver-header">
        <UserRoundCheck size={20} aria-hidden className="mt-0.5 shrink-0" />
        <div>
          <h1>Approver — {mandate?.approver.role ?? 'Head Chef (demo)'}</h1>
          <p>Separate human sign-off channel · approve trade-offs only, never missing evidence.</p>
        </div>
        {mandates.data && mandates.data.length > 1 && (
          <label className="approver-switch">
            <span>Signed in as</span>
            <select
              value={customerId}
              onChange={(e) => {
                setCustomerId(e.target.value)
                setSelectedId(undefined)
              }}
            >
              {mandates.data.map((m) => (
                <option key={m.customerId} value={m.customerId}>
                  {m.approver.role} · {m.organisationName}
                </option>
              ))}
            </select>
          </label>
        )}
      </header>

      <div className="approver-grid">
        <section className="panel" aria-labelledby="approver-queue">
          <h2
            id="approver-queue"
            className="panel-title"
            aria-label={`Proposals for you (${pending.length} waiting)`}
          >
            Queue ({pending.length} waiting)
          </h2>
          {items.length === 0 ? (
            <p className="muted small">Nothing to approve.</p>
          ) : (
            <ul className="approver-queue">
              {items.map(({ proposal }) => {
                // Enough to triage without opening the card (T45): who, what, why, and what
                // approving means. Each reason chip appears once.
                const reasons = [...approvalReasons(proposal), ...contextReasons(proposal)]
                const statement = rowStatement(proposal, mandate)
                const isSelected = proposal.proposalId === selected?.proposal.proposalId
                return (
                  <li key={proposal.proposalId}>
                    <button
                      type="button"
                      className={`queue-item ${isSelected ? 'selected' : ''}`}
                      aria-current={isSelected ? 'true' : undefined}
                      onClick={() => setSelectedId(proposal.proposalId)}
                    >
                      <span className="flex flex-wrap items-center justify-between gap-2">
                        <span className="inline-flex items-center gap-1.5">
                          {isSelected && (
                            <span className="queue-select-mark" aria-hidden="true">
                              <Check size={12} strokeWidth={3} />
                            </span>
                          )}
                          <strong>
                            {formatMoney(proposal.totals.exclVat)} · v{proposal.offerVersion}
                          </strong>
                        </span>
                        <span className="inline-flex items-center gap-1">
                          <span className={`queue-state queue-state-${proposal.state.toLowerCase()}`}>
                            {STATE_WORD[proposal.state] ?? proposal.state}
                          </span>
                          <ChevronRight size={14} aria-hidden className="queue-chevron" />
                        </span>
                      </span>
                      <span className="small font-semibold">
                        {mandate?.organisationName ?? proposal.customerId}
                      </span>
                      <span className="small" data-testid="queue-products">
                        {productSummary(proposal)}
                      </span>
                      {reasons.length > 0 && (
                        <span className="chip-list" aria-label="Reasons">
                          {reasons.map((code) => (
                            <span key={code} className="chip">
                              {reasonChip(code)}
                            </span>
                          ))}
                        </span>
                      )}
                      {statement && (
                        <span className="small" data-testid="queue-statement">
                          {statement}
                        </span>
                      )}
                      <span className="muted small">
                        {proposal.lines.length} line{proposal.lines.length === 1 ? '' : 's'} ·{' '}
                        {formatClock(proposal.createdAt)}
                      </span>
                    </button>
                  </li>
                )
              })}
            </ul>
          )}
        </section>

        <div className="approver-detail">
          {selected ? (
            <ApprovalCard
              key={`${selected.proposal.proposalId}-${selected.proposal.state}`}
              item={selected}
              mandate={mandate}
            />
          ) : (
            <p className="panel muted">Select a proposal.</p>
          )}
        </div>
      </div>
    </div>
  )
}
