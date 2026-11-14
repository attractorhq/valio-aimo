import { useId, useState } from 'react'
import { ChevronDown, ChevronUp, ShieldCheck, Sparkles } from 'lucide-react'
import type { Offer, Proposal, ReasonCode, RequestItem, StructuredRequest } from '../../lib/contracts'
import { formatQuantity, requirementLabel } from '../../lib/format'

export type CandidateTier = 'RECOMMENDED' | 'BEST' | 'BETTER' | 'GOOD' | 'ALTERNATIVE'

export type RankedCandidate = {
  productId: string
  name: string
  itemId: string
  rank: number
  tier: CandidateTier
  allRequirementsDeclared: boolean
  deliverableOnTimeVerified: boolean
  comparisonPriceExclVat?: string | null
  comparisonUnit?: string | null
  unitPriceExclVat?: string | null
  quantitySalesUnits?: string | null
  deliveredQuantity?: { value: string; unit: 'l' | 'kg' | 'pcs' | 'cases' } | null
  stockTier: string
  availableQuantity?: string | null
  deliveryDate?: string | null
  reliability?: string | null
  matchedPreferences?: number
  unknownFactCount?: number
  substitutionSource?: 'VALIO' | 'AGENT' | null
  substituteFor?: string | null
  tradeoff: string
  reasons?: ReasonCode[]
}

export type ItemPlan = {
  item: RequestItem
  resolvedProductType?: string | null
  shortlistIds?: string[]
  rankedCandidates: RankedCandidate[]
  partialCoverage?: boolean
}

const TIER_LABEL: Record<CandidateTier, string> = {
  RECOMMENDED: 'Recommended',
  BEST: 'Best',
  BETTER: 'Better',
  GOOD: 'Good',
  ALTERNATIVE: 'Alternative',
}

function tierBadgeClass(tier: CandidateTier): string {
  switch (tier) {
    case 'RECOMMENDED':
      return 'border-[var(--auto)] bg-[var(--auto-bg)] text-[var(--auto)]'
    case 'BEST':
      return 'border-[var(--info)] bg-[var(--info-bg)] text-[var(--info)]'
    case 'BETTER':
      return 'border-[var(--line)] bg-[var(--bg)] text-[var(--ink)]'
    case 'GOOD':
    default:
      return 'border-[var(--line)] bg-[var(--bg)] text-[var(--muted)]'
  }
}

/**
 * Synthetic mock alternatives used only when the UI runs in offline MOCK mode (`MOCK-P-A`, `MOCK-P-B`).
 * On the live/recorded `/v0.1` gateway, `plan` comes directly from `backend/app/agent/plan.py`.
 */
function buildSyntheticMockPlans(
  source: Proposal | Offer,
  request?: StructuredRequest,
): ItemPlan[] {
  if (!request?.items?.length) return []
  const isMockSource = source.lines.some((l) => l.productId.startsWith('MOCK-'))
  if (!isMockSource) return []

  return request.items.map((item) => {
    const line = source.lines.find((l) => l.itemId === item.itemId)
    if (line?.productId === 'MOCK-MILK') {
      return {
        item,
        rankedCandidates: [
          {
            productId: 'MOCK-MILK',
            name: 'Synthetic lactose-free milk drink 1 l',
            itemId: item.itemId,
            rank: 1,
            tier: 'RECOMMENDED',
            allRequirementsDeclared: true,
            deliverableOnTimeVerified: true,
            comparisonPriceExclVat: '1.01',
            comparisonUnit: 'l',
            unitPriceExclVat: '1.01',
            stockTier: 'EXACT',
            availableQuantity: '2000',
            tradeoff:
              '€1.01/l · 1 l / pc · Synthetic Dairy · semi-skimmed · 2000 in stock (verified) · 100 % reliability',
          },
          {
            productId: 'MOCK-MILK-SKIM',
            name: 'Synthetic lactose-free skimmed milk drink 1 l',
            itemId: item.itemId,
            rank: 2,
            tier: 'BEST',
            allRequirementsDeclared: true,
            deliverableOnTimeVerified: true,
            comparisonPriceExclVat: '1.10',
            comparisonUnit: 'l',
            unitPriceExclVat: '1.10',
            stockTier: 'PLENTY',
            tradeoff:
              '€1.10/l (+€0.09/l vs recommended) · 1 l / pc · Synthetic Dairy · skimmed (0 % fat) · plenty in stock (verified) · 100 % reliability',
          },
          {
            productId: 'MOCK-MILK-3PCT',
            name: 'Synthetic lactose-free 3 % milk drink 1 l',
            itemId: item.itemId,
            rank: 3,
            tier: 'BETTER',
            allRequirementsDeclared: true,
            deliverableOnTimeVerified: true,
            comparisonPriceExclVat: '1.40',
            comparisonUnit: 'l',
            unitPriceExclVat: '1.40',
            stockTier: 'PLENTY',
            tradeoff:
              '€1.40/l (+€0.39/l vs recommended) · 1 l / pc · Synthetic Dairy · 3 % fat · plenty in stock (verified) · 100 % reliability',
          },
          {
            productId: 'MOCK-MILK-UHT',
            name: 'Synthetic lactose-free semi-skimmed milk 1 l UHT',
            itemId: item.itemId,
            rank: 4,
            tier: 'GOOD',
            allRequirementsDeclared: true,
            deliverableOnTimeVerified: true,
            comparisonPriceExclVat: '1.56',
            comparisonUnit: 'l',
            unitPriceExclVat: '1.56',
            stockTier: 'PLENTY',
            tradeoff:
              '€1.56/l (+€0.55/l vs recommended) · 1 l / pc · Synthetic Dairy · semi-skimmed, UHT · plenty in stock (verified) · 100 % reliability',
          },
        ],
      }
    }

    if (line?.productId === 'MOCK-PASTA') {
      return {
        item,
        rankedCandidates: [
          {
            productId: 'MOCK-PASTA',
            name: 'Synthetic gluten-free macaroni 5 kg',
            itemId: item.itemId,
            rank: 1,
            tier: 'RECOMMENDED',
            allRequirementsDeclared: true,
            deliverableOnTimeVerified: true,
            comparisonPriceExclVat: '4.58',
            comparisonUnit: 'kg',
            unitPriceExclVat: '22.92',
            stockTier: 'EXACT',
            availableQuantity: '19',
            tradeoff:
              '€4.58/kg · 5 kg / bag · Synthetic Pasta · macaroni · 19 in stock (verified) · 100 % reliability',
          },
          {
            productId: 'MOCK-PASTA-PENNE',
            name: 'Synthetic gluten-free penne 500 g',
            itemId: item.itemId,
            rank: 2,
            tier: 'BEST',
            allRequirementsDeclared: true,
            deliverableOnTimeVerified: true,
            comparisonPriceExclVat: '5.20',
            comparisonUnit: 'kg',
            unitPriceExclVat: '2.60',
            stockTier: 'EXACT',
            availableQuantity: '399',
            substitutionSource: 'AGENT',
            tradeoff:
              'Agent substitute (closest compliant product type) · different product type: penne, not macaroni · €5.20/kg (+€0.62/kg vs recommended) · 0.5 kg / bag · Synthetic Pasta · penne · 399 in stock (verified) · 100 % reliability',
          },
          {
            productId: 'MOCK-PASTA-FETT',
            name: 'Synthetic organic gluten-free fettuccine 200 g',
            itemId: item.itemId,
            rank: 3,
            tier: 'BETTER',
            allRequirementsDeclared: true,
            deliverableOnTimeVerified: true,
            comparisonPriceExclVat: '16.50',
            comparisonUnit: 'kg',
            unitPriceExclVat: '3.30',
            stockTier: 'EXACT',
            availableQuantity: '4',
            substitutionSource: 'AGENT',
            tradeoff:
              'Agent substitute (closest compliant product type) · different product type: fettuccine, not macaroni · €16.50/kg (+€11.92/kg vs recommended) · 0.2 kg / pc · Synthetic Pasta · fettuccine, organic · 4 in stock (verified)',
          },
        ],
      }
    }

    if (line?.productId === 'MOCK-OAT') {
      return {
        item,
        rankedCandidates: [
          {
            productId: 'MOCK-OAT',
            name: 'Synthetic gluten-free oat drink 1 l',
            itemId: item.itemId,
            rank: 1,
            tier: 'RECOMMENDED',
            allRequirementsDeclared: true,
            deliverableOnTimeVerified: true,
            comparisonPriceExclVat: '1.17',
            comparisonUnit: 'l',
            unitPriceExclVat: '1.17',
            stockTier: 'EXACT',
            availableQuantity: '200',
            tradeoff:
              '€1.17/l · 1 l / pc · Synthetic Oat · UHT · 200 in stock (verified) · 99.7 % reliability',
          },
          {
            productId: 'MOCK-OAT-BARISTA',
            name: 'Synthetic gluten-free Barista oat drink 1 l UHT',
            itemId: item.itemId,
            rank: 2,
            tier: 'BEST',
            allRequirementsDeclared: true,
            deliverableOnTimeVerified: true,
            comparisonPriceExclVat: '1.37',
            comparisonUnit: 'l',
            unitPriceExclVat: '1.37',
            stockTier: 'PLENTY',
            tradeoff:
              '€1.37/l (+€0.20/l vs recommended) · 1 l / pc · Synthetic Oat · Barista, UHT · plenty in stock (verified) · 100 % reliability',
          },
          {
            productId: 'MOCK-OAT-PRO',
            name: 'Synthetic Professional Barista oat drink gluten-free 1 l UHT',
            itemId: item.itemId,
            rank: 3,
            tier: 'BETTER',
            allRequirementsDeclared: true,
            deliverableOnTimeVerified: true,
            comparisonPriceExclVat: '1.74',
            comparisonUnit: 'l',
            unitPriceExclVat: '1.74',
            stockTier: 'EXACT',
            availableQuantity: '130',
            tradeoff:
              '€1.74/l (+€0.57/l vs recommended) · 1 l / pc · Synthetic Oat · Barista, UHT · 130 in stock (verified)',
          },
          {
            productId: 'MOCK-OAT-CAFE',
            name: 'Synthetic Café Barista oat drink gluten-free 1 l',
            itemId: item.itemId,
            rank: 4,
            tier: 'GOOD',
            allRequirementsDeclared: true,
            deliverableOnTimeVerified: true,
            comparisonPriceExclVat: '2.08',
            comparisonUnit: 'l',
            unitPriceExclVat: '2.08',
            stockTier: 'EXACT',
            availableQuantity: '520',
            tradeoff:
              '€2.08/l (+€0.91/l vs recommended) · 1 l / pc · Synthetic Oat · Barista · 520 in stock (verified)',
          },
        ],
      }
    }

    return { item, rankedCandidates: [] }
  })
}

function normalisePlanEntries(
  rawPlan: unknown[] | null | undefined,
  source: Proposal | Offer,
  request?: StructuredRequest,
): ItemPlan[] {
  if (Array.isArray(rawPlan) && rawPlan.length > 0) {
    const typed = rawPlan.filter(
      (entry): entry is ItemPlan =>
        Boolean(entry && typeof entry === 'object' && 'item' in entry && 'rankedCandidates' in entry),
    )
    if (typed.some((p) => p.rankedCandidates.some((rc) => Boolean(rc.tier)))) {
      return typed
    }
  }
  return buildSyntheticMockPlans(source, request)
}

/**
 * Step 3 Good / Better / Best alternatives per item (brief §7.6, I3 response logic, T22).
 * Shows Recommended + Best / Better / Good options with a one-line trade-off for each.
 * Only proven-compliant products are ever included; non-compliant products remain in Excluded.
 */
export function Alternatives({
  plan,
  proposal,
  offer,
  request: directRequest,
}: {
  plan?: unknown[] | null
  proposal?: Proposal
  offer?: Proposal | Offer
  request?: StructuredRequest
}) {
  const [expanded, setExpanded] = useState(false)
  const detailsId = useId()
  const source = proposal ?? offer
  if (!source) return null
  if (proposal?.decision === 'BLOCK') return null

  const req = directRequest ?? proposal?.request
  const resolvedPlans = normalisePlanEntries(plan, source, req)
  const itemsWithOptions = resolvedPlans
    .map((entry) => ({
      ...entry,
      topOptions: entry.rankedCandidates
        .filter(
          (rc) =>
            rc.tier === 'RECOMMENDED' ||
            rc.tier === 'BEST' ||
            rc.tier === 'BETTER' ||
            rc.tier === 'GOOD',
        )
        .slice(0, 4),
    }))
    .filter((entry) => entry.topOptions.length >= 2)

  if (itemsWithOptions.length === 0) return null

  const ToggleIcon = expanded ? ChevronUp : ChevronDown
  const uniqueRequirements = Array.from(
    new Map(
      itemsWithOptions
        .flatMap((entry) => entry.item.requirements ?? [])
        .map((r) => [`${r.kind}-${r.code}`, r]),
    ).values(),
  )

  return (
    <section
      className="mt-1 min-w-0 rounded-lg border border-[var(--line)] bg-[var(--bg)] px-2 py-0.5 [overflow-wrap:anywhere]"
      aria-label="Good / Better / Best alternatives"
    >
      <div className="flex flex-wrap items-center justify-between gap-1">
        <div className="flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-0.5 text-[11px] leading-none">
          <h3 className="inline-flex items-center gap-1 font-bold text-[var(--ink)]">
            <Sparkles size={11} aria-hidden />
            <span>Alternatives</span>
          </h3>
          {uniqueRequirements.map((r) => (
            <span
              key={`${r.kind}-${r.code}`}
              className="inline-flex items-center gap-0.5 rounded bg-[var(--auto-bg)] px-1 py-0.5 text-[10px] font-semibold text-[var(--auto)]"
            >
              <ShieldCheck size={10} aria-hidden />
              <span>{requirementLabel(r)} proven</span>
            </span>
          ))}
          {itemsWithOptions.map((entry) => (
            <span
              key={entry.item.itemId}
              className="inline-flex flex-wrap items-center gap-0.5 text-[10px]"
              aria-label={`Summary alternatives for ${entry.item.what}`}
            >
              <strong className="text-[var(--ink)]">{entry.item.what}:</strong>
              {entry.topOptions.map((cand) => (
                <span
                  key={cand.productId}
                  title={`${cand.name} (${cand.productId}) — ${cand.tradeoff}`}
                  className={`inline-flex items-center gap-0.5 rounded border px-1 py-0.5 text-[10px] font-semibold ${tierBadgeClass(cand.tier)}`}
                >
                  <span>{TIER_LABEL[cand.tier]}</span>
                  {!cand.productId.startsWith('MOCK-') && <span>{cand.productId}</span>}
                </span>
              ))}
            </span>
          ))}
        </div>

        <button
          type="button"
          aria-expanded={expanded}
          aria-controls={detailsId}
          onClick={() => setExpanded((prev) => !prev)}
          className="inline-flex items-center gap-0.5 rounded border border-[var(--line)] bg-[var(--surface)] px-1.5 py-0.5 text-[10px] font-semibold text-[var(--ink)] hover:border-[var(--green)]"
        >
          <ToggleIcon size={11} aria-hidden />
          <span>{expanded ? 'Hide trade-offs' : 'Show trade-offs'}</span>
        </button>
      </div>

      {expanded && (
        <div
          id={detailsId}
          className={`mt-2 grid gap-2 border-t border-[var(--line)] pt-2 ${itemsWithOptions.length > 1 ? 'lg:grid-cols-2' : ''}`}
        >
          {itemsWithOptions.map((entry) => (
            <div
              key={entry.item.itemId}
              className="min-w-0 rounded-lg border border-[var(--line)] bg-[var(--surface)] px-2.5 py-1.5"
              aria-label={`Alternatives for ${entry.item.what}`}
            >
              <div className="flex flex-wrap items-center justify-between gap-1.5 border-b border-[var(--line)] pb-1">
                <div className="text-xs">
                  <strong className="text-[var(--ink)]">{entry.item.what}</strong>
                  <span className="text-[var(--muted)]">
                    {' '}
                    · {formatQuantity(entry.item.quantity, entry.item.unit)}
                  </span>
                </div>
              </div>

              <ol className="mt-1 grid gap-1" aria-label={`Ranked options for ${entry.item.what}`}>
                {entry.topOptions.map((cand) => (
                  <li
                    key={cand.productId}
                    className="rounded border border-[var(--line)] bg-[var(--bg)] px-2 py-1 text-xs"
                  >
                    <div className="flex flex-wrap items-center justify-between gap-1.5">
                      <div className="flex min-w-0 flex-wrap items-center gap-1.5">
                        <span
                          className={`inline-flex items-center rounded border px-1.5 py-0.5 text-[10px] font-bold tracking-wide uppercase ${tierBadgeClass(cand.tier)}`}
                        >
                          {TIER_LABEL[cand.tier]}
                        </span>
                        <span className="font-semibold text-[var(--ink)]">{cand.name}</span>
                        <span className="text-[var(--muted)]">· {cand.productId}</span>
                      </div>
                      {cand.substitutionSource && (
                        <span className="rounded border border-[var(--ask)] bg-[var(--ask-bg)] px-1.5 py-0.5 text-[10px] font-semibold text-[var(--ask)]">
                          {cand.substitutionSource === 'VALIO' ? 'Valio substitute' : 'Agent substitute'}
                        </span>
                      )}
                    </div>
                    <p className="mt-0.5 text-[11px] leading-snug text-[var(--muted)]">
                      {cand.tradeoff}
                    </p>
                  </li>
                ))}
              </ol>
            </div>
          ))}
        </div>
      )}
    </section>
  )
}
