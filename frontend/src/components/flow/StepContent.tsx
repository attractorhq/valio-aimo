import { Ban, CheckCircle2, Hourglass, Info } from 'lucide-react'
import type { Mandate, Proposal, ReasonCode } from '../../lib/contracts'
import { REASON_REGISTRY } from '../../lib/contracts'
import {
  committedLimitSentence,
  explainWhyNotAuto,
  plainReason,
  uniqueReasons,
} from '../approver/explain'
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
import { isProven, proposalRequirementChecks } from '../../lib/requirements'

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
        ? `Approved by ${proposal.approverRole ?? 'the approver'} → Confirmed`
        : 'Agent decided → Confirmed'
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
  const committed = committedLimitSentence(code, proposal, mandate)
  if (committed) return committed
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
          {decision === 'AUTO' &&
            'Every required fact is verified or declared, inside the mandate.'}
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
    </div>
  )
}

function resultTone(proposal: Proposal): 'auto' | 'ask' | 'block' {
  if (proposal.state === 'CONFIRMED_SIMULATED') return 'auto'
  if (['REJECTED', 'EXPIRED', 'SUPERSEDED'].includes(proposal.state)) return 'block'
  return proposal.decision === 'AUTO' ? 'auto' : proposal.decision === 'ASK' ? 'ask' : 'block'
}

function deliverySummary(proposal: Proposal) {
  const dates = [...new Set(proposal.lines.map((line) => line.deliveryDate))].sort()
  return dates.map(formatDate).join(', ')
}

function resultBullets(proposal: Proposal, mandate?: Mandate) {
  const decisionReasons = uniqueReasons(proposal.reasons)
    .filter(
      (reason) => proposal.state !== 'PENDING_APPROVAL' || REASON_REGISTRY[reason].effect !== 'ASK',
    )
    .map((reason) => reasonChip(reason))
  if (resultTone(proposal) === 'block') return decisionReasons.slice(0, 4)

  const checks = proposalRequirementChecks(proposal, mandate?.forbiddenAllergens)
  const requirements = [
    ...new Set(checks.map(({ requirement }) => requirementLabel(requirement))),
  ].map((label) => {
    const matching = checks.filter(({ requirement }) => requirementLabel(requirement) === label)
    return matching.every(({ proven }) => proven)
      ? `${label}: ${matching.every(({ fact }) => fact?.level === 'VERIFIED') ? 'checked' : 'stated in product data'}`
      : `${label}: evidence incomplete`
  })
  const allergens = proposal.lines
    .flatMap((line) => line.facts)
    .filter(
      (fact) =>
        fact.fact.startsWith('allergen.') &&
        !fact.conflict &&
        fact.level !== 'UNKNOWN' &&
        ['CONTAINS', 'MAY_CONTAIN'].includes(String(fact.value)),
    )
    .map((fact) => fact.display)
    .filter(Boolean)
  const cutoff = proposal.lines
    .flatMap((line) => (line.closingTime ? [line.closingTime] : []))
    .sort((left, right) => Date.parse(left) - Date.parse(right))[0]
  const delivery = deliverySummary(proposal)
  return [...new Set([...requirements, ...allergens, ...decisionReasons])]
    .slice(0, delivery ? 3 : 4)
    .concat(
      delivery ? [`Delivery ${delivery}${cutoff ? ` (order by ${formatTime(cutoff)})` : ''}`] : [],
    )
}

export function ResultBanner({
  proposal,
  mandate,
  evidenceExpanded,
  onToggleEvidence,
}: {
  proposal: Proposal
  mandate?: Mandate
  evidenceExpanded: boolean
  onToggleEvidence: () => void
}) {
  const tone = resultTone(proposal)
  const Icon =
    tone === 'auto' ? CheckCircle2 : tone === 'ask' ? Hourglass : tone === 'block' ? Ban : Info
  const pending = proposal.state === 'PENDING_APPROVAL'
  const total = formatMoney(proposal.totals.exclVat)
  const delivery = deliverySummary(proposal)
  const first = proposal.lines[0]
  const item =
    first && proposal.request.items.find((requestItem) => requestItem.itemId === first.itemId)
  const product = item && !first.substitutionSource ? item.what : first?.name
  const qualifier = (item?.requirements ?? [])
    .filter(
      (requirement) =>
        requirement.kind === 'CLAIM' &&
        isProven(
          requirement,
          first?.facts.find((fact) => fact.fact === `claim.${requirement.code}`),
        ),
    )
    .map((requirement) => requirementLabel(requirement).toLowerCase())
    .filter((label) => !product?.toLowerCase().includes(label))
    .join(' ')
  const items =
    proposal.lines.length === 1 && first
      ? [
          formatQuantity(first.deliveredQuantity.value, first.deliveredQuantity.unit),
          qualifier,
          product,
        ]
          .filter(Boolean)
          .join(' ')
      : proposal.lines.length === 2
        ? proposal.lines
            .map((line) => {
              const requested = proposal.request.items.find((entry) => entry.itemId === line.itemId)
              return `${formatQuantity(line.deliveredQuantity.value, line.deliveredQuantity.unit)} ${line.substitutionSource ? line.name : (requested?.what ?? line.name)}`
            })
            .join(' and ')
        : `${proposal.lines.length} order lines`
  const blockReason = proposal.reasons.find((code) => REASON_REGISTRY[code].effect === 'BLOCK')
  const why = pending
    ? explainWhyNotAuto(proposal, mandate, { omitTotalAmount: true })
    : proposal.state === 'BLOCKED' && blockReason
      ? plainReason(blockReason, proposal, mandate)
      : null
  const bullets = resultBullets(proposal, mandate)
  const next =
    proposal.state === 'CONFIRMED_SIMULATED'
      ? 'Ordered'
      : pending
        ? `Needs ${proposal.approverRole ?? mandate?.approver.role ?? 'the approver'}’s OK`
        : proposal.state === 'BLOCKED'
          ? 'Order blocked'
          : approvalLabel(proposal)
  const summary =
    tone === 'block'
      ? `${next}. ${total} excl. VAT.`
      : `${next}: ${items}${delivery ? `, ${delivery}` : ''}, ${total} excl. VAT.`
  return (
    <section
      className={`outcome outcome-${tone} result-banner`}
      aria-label="Result"
      data-testid="result-banner"
    >
      <p className="outcome-headline" data-testid="result-summary">
        <Icon size={20} aria-hidden />
        <span>{summary}</span>
      </p>
      {why && (
        <p className="text-xs text-[var(--ink)]" data-testid="result-why">
          {why.charAt(0).toUpperCase() + why.slice(1).replace(/\.$/, '')}.
        </p>
      )}
      {bullets.length > 0 && (
        <ul className="result-bullets" aria-label="Decision reasons">
          {bullets.map((bullet) => (
            <li key={bullet}>{bullet}</li>
          ))}
        </ul>
      )}
      <button
        type="button"
        className="disclosure-btn result-evidence"
        aria-expanded={evidenceExpanded}
        aria-controls="purchase-evidence"
        onClick={onToggleEvidence}
      >
        {evidenceExpanded ? 'Hide evidence' : 'Show evidence'}
      </button>
    </section>
  )
}
