import { Ban, CheckCircle2, Hourglass, Info } from 'lucide-react'
import type { Mandate, Proposal } from '../../lib/contracts'
import { REASON_REGISTRY } from '../../lib/contracts'
import { explainWhyNotAuto, plainReason, uniqueReasons } from '../approver/explain'
import {
  formatDate,
  formatMoney,
  formatNumber,
  formatQuantity,
  formatTime,
  reasonChip,
  requirementLabel,
} from '../../lib/format'
import { proposalRequirementChecks } from '../../lib/requirements'
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

/** "(1,000 × 1 l)": whole packs times the content of one, when the delivered quantity is exactly that. */
function packText(line: Proposal['lines'][number]): string | null {
  const content = Number(line.contentPerSalesUnit.value)
  const units = Number(line.quantitySalesUnits)
  if (!(content > 0) || line.contentPerSalesUnit.unit !== line.deliveredQuantity.unit) return null
  if (Math.abs(content * units - Number(line.deliveredQuantity.value)) >= 0.001) return null
  // A product sold by the kilo in 1 kg steps has no pack worth naming.
  if (content === 1 && line.salesUnit.toUpperCase() === line.contentPerSalesUnit.unit.toUpperCase()) return null
  const size = formatNumber(String(Math.round(content * 1000) / 1000))
  return `(${formatNumber(line.quantitySalesUnits)} × ${formatQuantity(size, line.contentPerSalesUnit.unit)})`
}

/**
 * T112: the headline names what was bought: "1,000 l · Valio Eila® semi skimmed milk drink 1 l lactose free
 * (1,000 × 1 l)". Several lines: the first product and "and N more".
 */
export function orderedProducts(proposal: Proposal): string {
  const first = proposal.lines[0]
  if (!first) return ''
  const name = first.name.replace(/\s+/g, ' ').trim()
  const head = [
    `${formatQuantity(first.deliveredQuantity.value, first.deliveredQuantity.unit)} ·`,
    name,
    packText(first),
  ]
    .filter(Boolean)
    .join(' ')
  const more = proposal.lines.length - 1
  if (more <= 0) return head
  // T121: one item split over several products leads with its total, never only the first line's quantity.
  const units = new Set(proposal.lines.map((line) => line.deliveredQuantity.unit))
  const items = new Set(proposal.lines.map((line) => line.itemId))
  if (units.size === 1 && items.size === 1) {
    const total = proposal.lines.reduce((sum, line) => sum + Number(line.deliveredQuantity.value), 0)
    return `${formatQuantity(String(Math.round(total * 1000) / 1000), first.deliveredQuantity.unit)} in total · ${name} and ${more} more`
  }
  return `${head} and ${more} more`
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
  const items = orderedProducts(proposal)
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
  // T112: the chosen product and pack on one line (ellipsis when it does not fit), then delivery and total.
  const what = tone === 'block' || !items ? `${next}.` : `${next}: ${items}`
  const rest = tone === 'block' || !items
    ? `${total} excl. VAT.`
    : `${delivery ? `${delivery}, ` : ''}${total} excl. VAT.`
  return (
    <section
      className={`outcome outcome-${tone} result-banner`}
      aria-label="Result"
      data-testid="result-banner"
    >
      <p className="outcome-headline" data-testid="result-summary">
        <Icon size={20} aria-hidden />
        <span className="result-summary-text">
          <span className="result-what" title={what}>{what}</span>{' '}
          <span className="result-rest">{rest}</span>
        </span>
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
