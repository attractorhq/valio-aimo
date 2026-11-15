import type { Offer, OfferLine, Proposal, StructuredRequest } from '../../lib/contracts'
import { formatMoney, formatQuantity } from '../../lib/format'
import { ProposalRefText } from '../trust/ProposalRefText'

const FROZEN_WORDS = ['frozen', 'pakaste', 'pakastettu', 'jäädytetty', 'fryst', 'iqf']

function linePricePerUnit(line: OfferLine): number | null {
  const contentVal = Number(line.contentPerSalesUnit.value)
  if (contentVal > 0) {
    return Number(line.unitPriceExclVat) / contentVal
  }
  const delivVal = Number(line.deliveredQuantity.value)
  if (delivVal > 0) {
    return Number(line.lineTotalExclVat) / delivVal
  }
  return null
}

function lineIsFrozen(line: OfferLine): boolean {
  const text = `${line.name} ${line.nameFi ?? ''}`.toLowerCase()
  return FROZEN_WORDS.some((w) => text.includes(w))
}

function splitItemPairs(
  source: Proposal | Offer,
  request?: StructuredRequest,
): Array<[OfferLine, OfferLine]> {
  const req = request ?? ('request' in source ? source.request : undefined)
  const byItem = new Map<string, OfferLine[]>()
  for (const line of source.lines) {
    const group = byItem.get(line.itemId) ?? []
    group.push(line)
    byItem.set(line.itemId, group)
  }

  const pairs: Array<[OfferLine, OfferLine]> = []
  for (const [itemId, itemLines] of byItem.entries()) {
    if (itemLines.length < 2) continue
    const reqItem = req?.items.find((i) => i.itemId === itemId)
    if (reqItem) {
      const delivTotal = itemLines.reduce((sum, l) => sum + Number(l.deliveredQuantity.value), 0)
      if (delivTotal < Number(reqItem.quantity)) continue
    }
    pairs.push([itemLines[0], itemLines[1]])
  }
  return pairs
}

/**
 * T69 plain-language split approval sentence:
 * "Approving means 70 kg of A + 50 kg of B (€0.40/kg more)."
 */
export function formatSplitApprovalSentence(
  source: Proposal | Offer,
  request?: StructuredRequest,
): string | null {
  const pairs = splitItemPairs(source, request)
  if (pairs.length === 0) return null

  const clauses = pairs.map(([lineA, lineB]) => {
    const priceA = linePricePerUnit(lineA)
    const priceB = linePricePerUnit(lineB)
    const unit = lineB.deliveredQuantity.unit
    let diffClause = ''
    if (priceA != null && priceB != null) {
      const diff = Math.round((priceB - priceA) * 100) / 100
      if (diff > 0) {
        diffClause = ` (${formatMoney(diff.toFixed(2))}/${unit} more)`
      } else if (diff < 0) {
        diffClause = ` (${formatMoney(Math.abs(diff).toFixed(2))}/${unit} less)`
      }
    }
    const partA = `${formatQuantity(lineA.deliveredQuantity.value, lineA.deliveredQuantity.unit)} of ${lineA.name}`
    const partB = `${formatQuantity(lineB.deliveredQuantity.value, lineB.deliveredQuantity.unit)} of ${lineB.name}${diffClause}`
    return `${partA} + ${partB}`
  })

  return `Approving means ${clauses.join(', ')}.`
}

/**
 * T69 split detail lines:
 * - Price guard when line 2's price per kg/l is > 10 % above line 1's;
 * - Fresh vs. frozen trade-off when lines differ in state.
 */
export function formatSplitDetailLines(
  source: Proposal | Offer,
  request?: StructuredRequest,
): string[] {
  const pairs = splitItemPairs(source, request)
  const details: string[] = []

  for (const [lineA, lineB] of pairs) {
    const heldFact = lineA.facts.find((f) => f.fact === 'stock.heldByYourOrders')?.display
    if (heldFact) {
      details.push(heldFact.endsWith('.') ? heldFact : `${heldFact}.`)
    }
    const factGuard = lineB.facts.find((f) => f.fact === 'split.priceGuard')?.display
    if (factGuard) {
      details.push(factGuard.endsWith('.') ? factGuard : `${factGuard}.`)
    } else {
      const priceA = linePricePerUnit(lineA)
      const priceB = linePricePerUnit(lineB)
      const unit = lineB.deliveredQuantity.unit
      if (priceA != null && priceB != null && priceA > 0 && priceB > priceA) {
        const pct = Math.round(((priceB - priceA) / priceA) * 1000) / 10
        if (pct > 10) {
          const pctStr = Number.isInteger(pct) ? String(pct) : pct.toFixed(1)
          const diff = Math.round((priceB - priceA) * 100) / 100
          details.push(
            `Price guard: +${pctStr} % per ${unit} above primary line (${formatMoney(priceB.toFixed(2))}/${unit} vs ${formatMoney(priceA.toFixed(2))}/${unit}, +${formatMoney(diff.toFixed(2))}/${unit}).`,
          )
        }
      }
    }

    const factVariant = lineB.facts.find((f) => f.fact === 'split.variant')?.display
    if (factVariant) {
      details.push(factVariant.endsWith('.') ? factVariant : `${factVariant}.`)
    } else if (lineIsFrozen(lineA) !== lineIsFrozen(lineB)) {
      details.push(
        lineIsFrozen(lineB)
          ? 'Line 2 is frozen; line 1 is fresh.'
          : 'Line 2 is fresh; line 1 is frozen.',
      )
    }
  }

  return details
}

export function formatFullSplitApprovalStatement(
  source: Proposal | Offer,
  request?: StructuredRequest,
): string | null {
  const sentence = formatSplitApprovalSentence(source, request)
  if (!sentence) return null
  const details = formatSplitDetailLines(source, request)
  return details.length > 0 ? `${sentence} ${details.join(' ')}` : sentence
}

export function SplitProposalSummary({
  proposal,
  offer,
  request,
}: {
  proposal?: Proposal
  offer?: Proposal | Offer
  request?: StructuredRequest
}) {
  const source = proposal ?? offer
  if (!source) return null
  const sentence = formatSplitApprovalSentence(source, request)
  if (!sentence) return null
  const details = formatSplitDetailLines(source, request)

  return (
    <div
      className="my-2 border-b border-[var(--line)] pb-2 text-sm text-[var(--ink)]"
      role="note"
      aria-label="Split top-up summary"
      data-testid="split-proposal-summary"
    >
      <p className="font-semibold" data-testid="split-approval-sentence">
        {sentence}
      </p>
      {details.map((detail, idx) => (
        <p key={idx} className="mt-0.5 text-xs text-[var(--ink)]" data-testid="split-detail-line">
          <ProposalRefText text={detail} />
        </p>
      ))}
    </div>
  )
}
