import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Check, ChevronRight, UserRoundCheck } from 'lucide-react'
import { approverChannel, gateway, type ApproverItem } from '../lib/gateway'
import { formatClock, formatMoney, reasonChip } from '../lib/format'
import { ApprovalCard } from '../components/approver/ApprovalCard'
import { LoadingSection, Skeleton } from '../components/Loading'
import { chooseApproverRole, chosenApproverRole } from '../lib/demoAccess'
import { ApproverPasskeyControl } from './ApproverPasskey'
import {
  approvalReasons,
  contextReasons,
  productSummary,
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

  useEffect(() => {
    if (customerId) return
    const saved = mandates.data?.find((m) => m.approver.role === chosenApproverRole())
    if (saved) setCustomerId(saved.customerId)
  }, [customerId, mandates.data])

  const mandate = mandates.data?.find((m) => m.customerId === customerId)
  const list = useQuery({
    queryKey: ['approver-proposals', customerId],
    queryFn: () => approverChannel.listProposals(customerId!),
    enabled: Boolean(customerId),
    refetchInterval: 2000,
  })
  const items = list.data ?? []
  const loading = mandates.isPending || list.isPending
  const error = mandates.error ?? list.error
  const pending = items.filter((i) => i.proposal.state === 'PENDING_APPROVAL')
  const selected: ApproverItem | undefined =
    items.find((i) => i.proposal.proposalId === selectedId) ?? pending[0] ?? items[0]

  return (
    <div className="approver-page">
      <header className="approver-header">
        <UserRoundCheck size={20} aria-hidden className="mt-0.5 shrink-0" />
        <div>
          <h1>Approver{mandate ? ` — ${mandate.approver.role}` : ''}</h1>
          <p>Human sign-off channel · not the agent</p>
          <p>Sign-in: role, or a passkey on this phone</p>
          {/* Reserve the control's height so it does not shift the page when the mandate arrives (T84). */}
          <div className="mt-1.5" style={{ minHeight: 32 }}>
            {mandate && <ApproverPasskeyControl role={mandate.approver.role} />}
          </div>
        </div>
        {mandates.isPending ? (
          <label className="approver-switch" aria-hidden="true">
            <span>Signed in as</span>
            <Skeleton className="skeleton-select" />
          </label>
        ) : (
          mandates.data &&
          mandates.data.length > 1 && (
            <label className="approver-switch">
              <span>Signed in as</span>
              <select
                value={customerId ?? ''}
                onChange={(e) => {
                  const chosen = mandates.data.find((m) => m.customerId === e.target.value)
                  if (!chosen) return
                  chooseApproverRole(chosen.approver.role)
                  setCustomerId(e.target.value)
                  setSelectedId(undefined)
                }}
              >
                <option value="" disabled>Choose your role</option>
                {mandates.data.map((m) => {
                  return (
                    <option key={m.customerId} value={m.customerId}>
                      {m.approver.role} · {m.organisationName}
                    </option>
                  )
                })}
              </select>
            </label>
          )
        )}
      </header>

      {!customerId ? mandates.error ? (
        <p className="notice notice-error" role="alert">Could not load the approver queue.</p>
      ) : mandates.isPending && chosenApproverRole() ? (
        <LoadingSection label="approver queue" className="panel" />
      ) : <p className="panel muted">Choose your approver role to see your buyer’s proposals.</p> : <div className="approver-grid">
        <section className="panel" aria-labelledby="approver-queue">
          <h2
            id="approver-queue"
            className="panel-title"
            aria-label={
              loading ? 'Proposals for you' : `Proposals for you (${pending.length} waiting)`
            }
          >
            {loading ? 'Queue' : `Queue (${pending.length} waiting)`}
          </h2>
          {error ? (
            <p className="notice notice-error" role="alert">
              Could not load the approver queue.
            </p>
          ) : loading ? (
            <LoadingSection label="approver queue" />
          ) : items.length === 0 ? (
            <p className="muted small">Nothing to approve.</p>
          ) : (
            <ul className="approver-queue">
              {items.map(({ proposal }) => {
                // Enough to triage without opening the card (T45): who, what and why, each reason chip once.
                // What approving means is said once, in the selected card's approval statement (T83).
                const reasons = [...approvalReasons(proposal), ...contextReasons(proposal)]
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
                          <span
                            className={`queue-state queue-state-${proposal.state.toLowerCase()}`}
                          >
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
          {loading && !error ? (
            <LoadingSection label="approval details" variant="approval" className="panel" />
          ) : selected ? (
            <ApprovalCard
              key={selected.proposal.proposalId}
              item={selected}
              mandate={mandate}
              onDecisionStart={() => setSelectedId(selected.proposal.proposalId)}
            />
          ) : !error ? (
            <p className="panel muted">Select a proposal.</p>
          ) : null}
        </div>
      </div>}
    </div>
  )
}
