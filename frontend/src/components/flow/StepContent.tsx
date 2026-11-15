import { Ban, CheckCircle2, Hourglass, Info } from 'lucide-react'
import type { Mandate, Proposal } from '../../lib/contracts'
import { REASON_REGISTRY } from '../../lib/contracts'
import { explainWhyNotAuto, plainReason, uniqueReasons } from '../approver/explain'
import {
  formatDate,
  formatMoney,
  formatQuantity,
  formatTime,
  reasonChip,
  requirementLabel,
} from '../../lib/format'
import { isProven, proposalRequirementChecks } from '../../lib/requirements'
import { ProposalRefText } from '../trust/ProposalRefText'
import { earlyDeliverySentence, overshootNotes } from '../../lib/clarity'
import { splitReasonLines } from '../proposal/SplitProposalSummary'

export { RequestCard } from '../request/RequestCard'
export { NoOrderBanner } from './NoOrderBanner'

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

function resultTone(proposal: Proposal): 'auto' | 'ask' | 'block' {
  if (proposal.state === 'CONFIRMED_SIMULATED') return 'auto'
  if (['REJECTED', 'EXPIRED', 'SUPERSEDED'].includes(proposal.state)) return 'block'
  return proposal.decision === 'AUTO' ? 'auto' : proposal.decision === 'ASK' ? 'ask' : 'block'
}

function deliverySummary(proposal: Proposal) {
  const dates = [...new Set(proposal.lines.map((line) => line.deliveryDate))].sort()
  return dates.map(formatDate).join(', ')
}

/** T109: "Valio offers delivery on Mon 16 Nov for this product; it arrives 3 days before Thursday." */
function deliveryBullet(delivery: string, early: string | null) {
  return early ?? `Delivery ${delivery}`
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
  const early = earlyDeliverySentence(proposal.lines, proposal.requestedDate)
  const orderBy = cutoff
    ? `${early ? ' Order' : ' (order'} by ${formatTime(cutoff)}${early ? '.' : ')'}`
    : ''
  return [...new Set([...requirements, ...allergens, ...decisionReasons])]
    .slice(0, delivery ? 3 : 4)
    .concat(delivery ? [`${deliveryBullet(delivery, early)}${orderBy}`] : [])
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
  // T109: why the order was split and why the quantity is above the amount asked, when the facts say so.
  const notes = [
    ...splitReasonLines(proposal),
    ...[...overshootNotes(proposal.lines, proposal.request).values()].map((note) => `${note}.`),
  ]
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
          <ProposalRefText text={`${why.charAt(0).toUpperCase() + why.slice(1).replace(/\.$/, '')}.`} />
        </p>
      )}
      {tone !== 'block' && notes.length > 0 && (
        <p className="text-xs text-[var(--ink)]" data-testid="result-notes">
          <ProposalRefText text={notes.join(' ')} />
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
