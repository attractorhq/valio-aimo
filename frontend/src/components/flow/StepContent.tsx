import { Ban, CheckCircle2, Hourglass, Info, ShieldCheck, Quote } from 'lucide-react'
import type { Mandate, Proposal, ReasonCode } from '../../lib/contracts'
import { REASON_REGISTRY } from '../../lib/contracts'
import {
  formatClock,
  formatDate,
  formatMoney,
  formatNumber,
  formatQuantity,
  formatTime,
  reasonChip,
  reasonText,
  requirementLabel,
} from '../../lib/format'

/** Step 1: the structured request, always shown (read-only in this version). */
export function RequestCard({ proposal }: { proposal: Proposal }) {
  const { request } = proposal
  return (
    <div className="request-card">
      <figure className="request-text">
        <Quote size={14} aria-hidden />
        <blockquote>{request.requestText}</blockquote>
        <figcaption>Original request · treated as data</figcaption>
      </figure>
      <table className="data-table">
        <caption className="visually-hidden">Structured request items</caption>
        <thead>
          <tr>
            <th scope="col">Item</th>
            <th scope="col">Quantity</th>
            <th scope="col">Hard requirements</th>
          </tr>
        </thead>
        <tbody>
          {request.items.map((item) => (
            <tr key={item.itemId}>
              <th scope="row">
                {item.what}
                {item.productId && <small className="muted"> · product {item.productId}</small>}
              </th>
              <td className="num">{formatQuantity(item.quantity, item.unit)}</td>
              <td>
                {item.requirements?.length ? (
                  <ul className="chip-list">
                    {item.requirements.map((r) => (
                      <li key={`${r.kind}-${r.code}`} className="chip chip-requirement">
                        {requirementLabel(r)}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <span className="muted">None</span>
                )}
                {item.preferences?.length ? (
                  <p className="muted small">
                    Preferences: {item.preferences.map((p) => p.value).join(', ')}
                  </p>
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <dl className="inline-facts">
        <div>
          <dt>Deliver by</dt>
          <dd>{formatDate(request.requestedDate)}</dd>
        </div>
        <div>
          <dt>Assumptions</dt>
          <dd>{request.assumptions?.length ? request.assumptions.join(' · ') : 'None'}</dd>
        </div>
      </dl>
    </div>
  )
}

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
        Prices fixed in offer v{proposal.offerVersion} until {formatClock(proposal.validUntil)} ·
        bound to mandate v{proposal.mandateVersion}
      </p>
    </div>
  )
}

export function approvalLabel(proposal: Proposal): string {
  switch (proposal.state) {
    case 'CONFIRMED_SIMULATED':
      return 'Agent decided → Confirmed (simulated)'
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
      return 'Superseded by a new proposal version'
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
  return /\{\w+\}/.test(text) ? reasonChip(code) : text
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
