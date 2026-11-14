import { Ban, CheckCircle2, Hourglass, Info, ShieldCheck } from 'lucide-react'
import type { Mandate, Proposal, ReasonCode } from '../../lib/contracts'
import { REASON_REGISTRY } from '../../lib/contracts'
import { explainWhyNotAuto, plainReason, uniqueReasons } from '../approver/explain'
import {
  formatClock,
  formatDate,
  formatMoney,
  formatNumber,
  formatQuantity,
  formatTime,
  reasonChip,
  reasonText,
} from '../../lib/format'

export { RequestCard } from '../request/RequestCard'

/** Step 2: what the lookup checked. Live price/availability is fetched for the shortlist only. */
export function LookupSummary({ proposal }: { proposal: Proposal }) {
  const excluded = proposal.excluded ?? []
  return (
    <ul className="lookup-list">
      {proposal.request.items.map((item) => {
        const chosen = proposal.lines.filter((l) => l.itemId === item.itemId)
        const out = excluded.filter((e) => e.itemId === item.itemId).length
        return (
          <li key={item.itemId}>
            <strong>{item.what}</strong>
            <span>
              {chosen.length ? chosen.map((l) => l.name).join(' + ') : 'No compliant product'}
            </span>
            <span className="muted">
              {out === 0
                ? 'No candidates excluded'
                : `${out} candidate${out === 1 ? '' : 's'} excluded`}
            </span>
          </li>
        )
      })}
    </ul>
  )
}

/** Step 3: the proposal at a fixed price. */
export function OrderProposal({ proposal }: { proposal: Proposal }) {
  return (
    <div>
      <div className="table-scroll">
        <table className="data-table order-table stack-on-mobile">
          <caption className="visually-hidden">Order proposal lines</caption>
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
                <td data-label="Delivery">
                  <span>
                    {formatDate(line.deliveryDate)}
                    {line.closingTime && (
                      <small className="muted block">order by {formatTime(line.closingTime)}</small>
                    )}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <dl className="totals">
        <div>
          <dt>Total excl. VAT</dt>
          <dd>{formatMoney(proposal.totals.exclVat)}</dd>
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
      <p className="muted small">
        Prices fixed until {formatClock(proposal.validUntil)} · mandate v{proposal.mandateVersion}
      </p>
    </div>
  )
}

export function approvalLabel(proposal: Proposal): string {
  switch (proposal.state) {
    case 'CONFIRMED_SIMULATED':
      return proposal.decision === 'ASK'
        ? `Approved by ${proposal.approverRole ?? 'the approver'} → Confirmed (simulated)`
        : 'Agent decided → Confirmed (simulated)'
    case 'PENDING_APPROVAL':
      return 'Waiting for approver'
    case 'BLOCKED':
      return 'Blocked by mandate'
    case 'APPROVED':
    case 'RECHECKED':
    case 'AUTO_APPROVED':
      return 'Approved · re-checking live'
    case 'REJECTED':
      return 'Rejected by approver'
    case 'EXPIRED':
      return 'Offer expired · re-quote needed'
    case 'SUPERSEDED':
      return 'Superseded by a new version'
    default:
      return 'Deciding'
  }
}

function explain(code: ReasonCode, proposal: Proposal, mandate?: Mandate): string {
  const params: Record<string, string> = { total: formatMoney(proposal.totals.exclVat) }
  if (mandate) {
    params.limit = formatMoney(
      code === 'OVER_HARD_LIMIT' ? mandate.hardLimitEur : mandate.autoApproveLimitEur,
    )
  }
  const text = reasonText(code, params)
  return /\{\w+\}/.test(text) ? (plainReason(code, proposal, mandate) ?? reasonChip(code)) : text
}

/** Step 4: the server's decision. The agent view never shows an approve button (P3). */
export function ApprovalOutcome({ proposal, mandate }: { proposal: Proposal; mandate?: Mandate }) {
  const decision = proposal.decision
  const approver = proposal.approverRole ?? mandate?.approver.role
  const Icon = decision === 'AUTO' ? CheckCircle2 : decision === 'ASK' ? Hourglass : Ban
  return (
    <div className={`outcome outcome-${decision.toLowerCase()}`}>
      <p className="outcome-headline">
        <Icon size={22} aria-hidden />
        <span>
          <span className="outcome-word">{decision}</span>
          {decision === 'AUTO' && 'Every fact is proven and the order is inside the mandate.'}
          {decision === 'ASK' && `Waiting for ${approver ?? 'the approver'}`}
          {decision === 'BLOCK' && 'This order breaks the mandate.'}
        </span>
      </p>
      {proposal.reasons.length > 0 && (
        <ul className="reason-list" aria-label="Reasons">
          {proposal.reasons.map((code) => {
            const effect = REASON_REGISTRY[code].effect
            return (
              <li key={code} className={`reason reason-${effect.toLowerCase()}`}>
                <span className="chip">
                  {effect === 'INFO' ? <Info size={13} aria-hidden /> : null}
                  {reasonChip(code)}
                </span>
                <span>{explain(code, proposal, mandate)}</span>
              </li>
            )
          })}
        </ul>
      )}
      {decision === 'ASK' && (
        <p className="muted small">
          The agent cannot approve. {approver ?? 'The approver'} signs off this exact version in the
          separate Approver panel.
        </p>
      )}
      {decision !== 'BLOCK' && (
        <p className="simulation-notice">
          <ShieldCheck size={14} aria-hidden />
          {proposal.simulationNotice ?? 'Simulated order — mock environment'}
        </p>
      )}
    </div>
  )
}

function resultTone(proposal: Proposal): 'auto' | 'ask' | 'block' {
  if (proposal.state === 'CONFIRMED_SIMULATED') return 'auto'
  if (['REJECTED', 'EXPIRED', 'SUPERSEDED'].includes(proposal.state)) return 'block'
  return proposal.decision === 'AUTO' ? 'auto' : proposal.decision === 'ASK' ? 'ask' : 'block'
}

/**
 * T43: the result first. Decision, total and order lines, then the reasons, with links down to the
 * evidence. Every detail stays in steps 1–4 below.
 */
export function ResultBanner({ proposal, logHref }: { proposal: Proposal; logHref?: string }) {
  const tone = resultTone(proposal)
  const Icon =
    tone === 'auto' ? CheckCircle2 : tone === 'ask' ? Hourglass : tone === 'block' ? Ban : Info
  return (
    <section
      className={`outcome outcome-${tone} result-banner`}
      aria-label="Result"
      data-testid="result-banner"
    >
      <div className="result-head">
        <p className="outcome-headline">
          <Icon size={22} aria-hidden />
          <span>
            <span className="outcome-word">{proposal.decision}</span>
            {approvalLabel(proposal)}
          </span>
        </p>
        <p className="result-total">
          <strong>{formatMoney(proposal.totals.exclVat)}</strong>
          <span> excl. VAT · {formatMoney(proposal.totals.inclVat)} incl. VAT</span>
        </p>
      </div>
      <ul className="result-lines" aria-label="Order lines">
        {proposal.lines.map((line) => (
          <li key={line.lineId}>
            <span>
              {formatNumber(line.quantitySalesUnits)} × {line.name}
            </span>
            <span className="num">{formatMoney(line.lineTotalExclVat)}</span>
          </li>
        ))}
      </ul>
      {proposal.decision === 'ASK' && proposal.state === 'PENDING_APPROVAL' && (
        <p className="text-xs text-[var(--ink)]" data-testid="result-why">
          Not auto-approved because {explainWhyNotAuto(proposal, undefined, { omitTotalAmount: true })}.
        </p>
      )}
      {proposal.reasons.length > 0 && (
        <ul className="chip-list" aria-label="Decision reasons">
          {uniqueReasons(proposal.reasons).map((code) => (
            <li key={code} className="chip bg-[var(--surface)]">
              {reasonChip(code)}
            </li>
          ))}
        </ul>
      )}
      <p className="result-links">
        <a href="#step-3">Order lines</a>
        <a href="#step-4">Reasons</a>
        {logHref && <a href={logHref}>Decision log</a>}
      </p>
    </section>
  )
}

/** The result when nothing could be ordered (no compliant product, or the run stopped). */
export function NoOrderBanner({ summary, detail }: { summary: string; detail?: string }) {
  return (
    <section
      className="outcome outcome-block result-banner"
      aria-label="Result"
      data-testid="result-banner"
    >
      <p className="outcome-headline">
        <Ban size={22} aria-hidden />
        <span>
          <span className="outcome-word">NO ORDER</span>
          {summary}
        </span>
      </p>
      {detail && <p className="mt-1 text-sm text-[var(--ink)]">{detail}</p>}
      <p className="result-links">
        <a href="#step-2">Excluded candidates</a>
      </p>
    </section>
  )
}
