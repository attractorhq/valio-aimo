import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle,
  Ban,
  CheckCircle2,
  Clock,
  Info,
  KeyRound,
  LoaderCircle,
  Smartphone,
  XCircle,
} from 'lucide-react'
import type { Mandate } from '../../lib/contracts'
import { approverChannel, GatewayError, type ApproverItem } from '../../lib/gateway'
import {
  formatClock,
  formatDate,
  formatMoney,
  formatNumber,
  formatQuantity,
  reasonChip,
} from '../../lib/format'
import { approvalReasons, contextReasons, formatApprovalStatement, plainReason } from './explain'
import { QrCode } from './QrCode'
import { logPath } from '../log/logData'
import { LoadingSection } from '../Loading'

/** What happened to this proposal version, in one line. */
export function ApprovalState({ item }: { item: ApproverItem }) {
  const { proposal, loggedAt } = item
  const logged = loggedAt ? ` · logged ${formatClock(loggedAt)}` : ''
  switch (proposal.state) {
    case 'CONFIRMED_SIMULATED':
      return (
        <p className="approval-state approval-state-done" role="status">
          <CheckCircle2 size={18} aria-hidden />
          Approved · {proposal.mode === 'LIVE' ? 're-checked live' : 're-checked (recorded data)'} ·
          Confirmed{logged}
        </p>
      )
    case 'REJECTED':
      return (
        <p className="approval-state approval-state-rejected" role="status">
          <XCircle size={18} aria-hidden />
          Rejected · nothing was ordered{logged}
        </p>
      )
    case 'EXPIRED':
      return (
        <p className="approval-state approval-state-void" role="status">
          <Clock size={18} aria-hidden />
          Expired · the offer ran out before sign-off and must be re-quoted.
        </p>
      )
    case 'SUPERSEDED':
      return (
        <p className="approval-state approval-state-void" role="status">
          <AlertTriangle size={18} aria-hidden />
          <span>
            Superseded · something changed at the re-check; new version
            {proposal.supersededBy ? ` (${proposal.supersededBy})` : ''} decided from scratch.
          </span>
        </p>
      )
    default:
      return null
  }
}

/** The exact proposal version the human is asked to sign off, with one Approve and one Reject. */
export function ApprovalCard({
  item,
  mandate,
  showQr = true,
  initialToken,
  initialApprovalStatement,
}: {
  item: ApproverItem
  mandate?: Mandate
  showQr?: boolean
  initialToken?: string
  initialApprovalStatement?: string | null
}) {
  const { proposal } = item
  const queryClient = useQueryClient()
  const [comment, setComment] = useState('')
  const pending = proposal.state === 'PENDING_APPROVAL'
  // The approval capability (demo token) is fetched on the approver channel only, for this version.
  const view = useQuery({
    queryKey: ['approver-view', proposal.proposalId],
    queryFn: () => approverChannel.view(proposal.proposalId),
    enabled: pending,
    staleTime: Infinity,
  })
  const activeToken = initialToken ?? view.data?.approvalToken
  const approvalStatement =
    view.data?.approvalStatement ??
    initialApprovalStatement ??
    formatApprovalStatement(proposal, mandate)
  const decide = useMutation({
    mutationFn: (action: 'APPROVE' | 'REJECT') =>
      approverChannel.decide({
        proposalId: proposal.proposalId,
        offerVersion: proposal.offerVersion,
        approvalToken: activeToken!,
        action,
        comment: comment.trim() || undefined,
      }),
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ['approver-proposals'] })
    },
  })
  const decided = decide.data ?? item
  const reasons = approvalReasons(proposal)
  const context = contextReasons(proposal)
  const onlyLimit = reasons.length === 1 && reasons[0] === 'OVER_AUTO_LIMIT'
  const total = formatMoney(proposal.totals.exclVat)
  const error = decide.error instanceof GatewayError ? decide.error : undefined

  if (pending && !initialToken && view.isPending) {
    return <LoadingSection label="approval details" variant="approval" className="panel" />
  }

  return (
    <article className="approval-card" aria-labelledby={`approve-${proposal.proposalId}`}>
      <header className="approval-card-header">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 id={`approve-${proposal.proposalId}`}>
            {pending
              ? `Approving version ${proposal.offerVersion}`
              : `Version ${proposal.offerVersion}`}
          </h2>
          <a
            href={logPath(proposal.proposalId)}
            className="text-xs font-semibold text-[var(--green)] underline"
          >
            Decision log
          </a>
        </div>
        {approvalStatement && !onlyLimit && (
          <p
            className="mt-1.5 border-t border-[var(--line)] pt-1.5 text-sm font-semibold text-[var(--ink)]"
            role="note"
            aria-label="Approval statement"
          >
            {approvalStatement}
          </p>
        )}
        <p className="muted small mt-0.5">
          {mandate?.organisationName ?? proposal.customerId} · {proposal.proposalId} · offer{' '}
          {proposal.offerId} v{proposal.offerVersion} · mandate v{proposal.mandateVersion}
        </p>
      </header>

      <section aria-label="Why you are asked" className="approval-why">
        {reasons.map((code) => (
          <p key={code} className="approval-reason">
            <span className="chip">{reasonChip(code)}</span>
            <span>
              {plainReason(code, proposal, mandate)}
              {onlyLimit && ' Everything else is verified or declared.'}
            </span>
          </p>
        ))}
        {context.map((code) => {
          const text = plainReason(code, proposal, mandate)
          return (
            <p key={code} className="approval-reason approval-reason-info">
              <span className="chip">
                <Info size={13} aria-hidden />
                {reasonChip(code)}
              </span>
              {text && <span>{text}</span>}
            </p>
          )
        })}
      </section>

      <div className="table-scroll">
        <table className="data-table stack-on-mobile">
          <caption className="visually-hidden">Exact lines you are approving</caption>
          <thead>
            <tr>
              <th scope="col">Product</th>
              <th scope="col" className="num">
                Quantity
              </th>
              <th scope="col" className="num">
                Unit price
              </th>
              <th scope="col" className="num">
                Line total
              </th>
              <th scope="col">Delivery</th>
            </tr>
          </thead>
          <tbody>
            {proposal.lines.map((line) => (
              <tr key={line.lineId}>
                <th scope="row">
                  {line.name}
                  <small className="muted"> · {line.productId}</small>
                </th>
                <td className="num" data-label="Quantity">
                  <span>
                    {formatNumber(line.quantitySalesUnits)} {line.salesUnitLabel}
                    <small className="muted block">
                      = {formatQuantity(line.deliveredQuantity.value, line.deliveredQuantity.unit)}
                    </small>
                  </span>
                </td>
                <td className="num" data-label="Unit price">
                  {formatMoney(line.unitPriceExclVat)}
                </td>
                <td className="num" data-label="Line total">
                  {formatMoney(line.lineTotalExclVat)}
                </td>
                <td data-label="Delivery">{formatDate(line.deliveryDate)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <dl className="totals">
        <div>
          <dt>Total excl. VAT</dt>
          <dd>{total}</dd>
        </div>
        <div>
          <dt>VAT</dt>
          <dd>{formatMoney(proposal.totals.vat)}</dd>
        </div>
        <div>
          <dt>Total incl. VAT</dt>
          <dd>{formatMoney(proposal.totals.inclVat)}</dd>
        </div>
      </dl>

      {decided.proposal.state === 'PENDING_APPROVAL' ? (
        <div className="approval-actions">
          <div className="approval-action-bar">
            <div className="approval-buttons">
              <button
                type="button"
                className="btn btn-approve"
                disabled={!activeToken || decide.isPending}
                aria-busy={decide.isPending && decide.variables === 'APPROVE'}
                onClick={() => decide.mutate('APPROVE')}
              >
                {decide.isPending && decide.variables === 'APPROVE' ? (
                  <LoaderCircle size={16} className="spin" aria-hidden />
                ) : (
                  <CheckCircle2 size={16} aria-hidden />
                )}
                Approve {total}
              </button>
              <button
                type="button"
                className="btn btn-reject"
                disabled={!activeToken || decide.isPending}
                onClick={() => decide.mutate('REJECT')}
              >
                {decide.isPending && decide.variables === 'REJECT' ? (
                  <LoaderCircle size={15} className="spin" aria-hidden />
                ) : (
                  <Ban size={15} aria-hidden />
                )}
                Reject
              </button>
            </div>
            <div className="approval-comment-field">
              <label htmlFor={`comment-${proposal.proposalId}`} className="field-label text-xs">
                Comment (optional)
              </label>
              <textarea
                id={`comment-${proposal.proposalId}`}
                rows={1}
                value={comment}
                onChange={(e) => setComment(e.target.value)}
                placeholder="Note for the audit log…"
              />
            </div>
          </div>
          {decide.isPending && (
            <p className="muted small" role="status">
              Re-checking live price &amp; availability…
            </p>
          )}
          {showQr && activeToken && (
            <aside className="approval-qr-box" aria-label="Phone approval QR code">
              <QrCode
                size={96}
                value={`${window.location.origin}/approve/${encodeURIComponent(activeToken)}?proposalId=${encodeURIComponent(proposal.proposalId)}`}
              />
              <div className="min-w-[180px] flex-1 text-xs">
                <div className="inline-flex items-center gap-1.5 font-bold text-[var(--ink)]">
                  <Smartphone size={13} aria-hidden />
                  <span>Phone sign-off</span>
                </div>
                <p className="mt-1">
                  <a
                    href={`/approve/${encodeURIComponent(activeToken)}?proposalId=${encodeURIComponent(proposal.proposalId)}`}
                    className="font-semibold text-[var(--green)] underline"
                  >
                    Open mobile approval page
                  </a>
                </p>
              </div>
            </aside>
          )}
        </div>
      ) : (
        <ApprovalState item={decided} />
      )}

      {error && (
        <p className="notice notice-error" role="alert">
          <AlertTriangle size={16} aria-hidden />
          {error.message}
        </p>
      )}
      {view.error && (
        <p className="notice notice-error" role="alert">
          <AlertTriangle size={16} aria-hidden />
          {view.error.message}
        </p>
      )}

      <p className="permission-notice">
        <KeyRound size={14} aria-hidden />
        {view.data?.permissionNotice ?? 'Single-use approval link'}
        {decided.proposal.state === 'PENDING_APPROVAL'
          ? view.data && ` · valid until ${formatClock(view.data.expiresAt)}`
          : ' · no longer valid'}
      </p>
    </article>
  )
}
