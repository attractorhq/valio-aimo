import { useId, useState, type ReactNode } from 'react'
import { AlertTriangle, ChevronDown, ChevronUp, ShieldAlert } from 'lucide-react'
import type {
  ExcludedProduct,
  OfferLine,
  RequestItem,
  TrustLabel,
  TrustLevel,
} from '../../lib/contracts'
import { liveCheckPhrase, requirementPhrase, substituteWhySentence } from '../../lib/clarity'
import { formatQuantity, reasonChip, requirementLabel } from '../../lib/format'
import { requirementChecks } from '../../lib/requirements'
import { ProposalRefText } from './ProposalRefText'
import { TrustBadge, TrustSourceMeta, formatFactDisplay } from './TrustBadge'

function countLevels(line: OfferLine): Record<TrustLevel, number> & { CONFLICT: number } {
  const counts: Record<TrustLevel, number> & { CONFLICT: number } = {
    VERIFIED: 0,
    DECLARED: 0,
    ESTIMATE: 0,
    UNKNOWN: 0,
    CONFLICT: 0,
  }
  counts[line.contentPerSalesUnit.level] += 1
  for (const f of line.facts) {
    counts[f.level] += 1
    if (f.conflict) counts.CONFLICT += 1
  }
  return counts
}

function factLabel(factName: string): string {
  if (factName === 'price.unit') return 'Unit price'
  if (factName === 'price.vatRate') return 'VAT rate'
  if (factName === 'price.comparison') return 'Comparison price'
  if (factName === 'stock.quantity') return 'Stock'
  if (factName === 'stock.heldByYourOrders') return 'Held by your orders'
  if (factName === 'delivery.date') return 'Delivery date'
  if (factName === 'delivery.cutoff') return 'Order cut-off'
  if (factName === 'content.perSalesUnit') return 'Pack content'
  if (factName === 'reliability') return 'Delivery reliability'
  if (factName === 'co2') return 'Carbon footprint'
  if (factName === 'origin') return 'Origin'
  if (factName === 'substitute.valio') return 'Valio substitute check'
  if (factName.startsWith('claim.')) return 'Claim'
  if (factName.startsWith('allergen.')) return 'Allergen'
  if (factName.startsWith('label.')) return 'Label'
  return factName
}

const SOURCE_NAMES: Record<string, string> = {
  allergen: 'supplier allergen list',
  nonAllergen: 'supplier free-from list',
  nutritionalClaim: 'supplier claim',
  packagingMarkedLabel: 'package label',
}

/** "synkkaData.classifications[nonAllergen]" → "supplier free-from list". */
function sourceShort(fact: TrustLabel): string {
  const field = fact.source.field ?? ''
  const group = /\[(\w+)\]$/.exec(field)?.[1] ?? field
  return SOURCE_NAMES[group] ?? (group || fact.source.system)
}

function sourceText(fact: TrustLabel): string {
  const field = fact.source.field ? ` · ${fact.source.field}` : ''
  const method = fact.method ? ` (${fact.method})` : ''
  return `${fact.source.system}${field}${method}`
}

export function TrustCard({
  line,
  item,
  forbiddenAllergens = [],
  excludedSubstitutes = [],
  showEvidence = false,
  mode,
  requestedDate,
  excluded = [],
}: {
  line: OfferLine
  item?: RequestItem
  /** The mandate's forbidden allergens: hard requirements the policy enforces on every line. */
  forbiddenAllergens?: readonly string[]
  excludedSubstitutes?: ExcludedProduct[]
  showEvidence?: boolean
  mode?: 'LIVE' | 'RECORDED'
  /** The order's requested date, for the substitute line's "arrives by" clause. */
  requestedDate?: string
  /** Every excluded candidate of the order; the substitute line counts those for its own item. */
  excluded?: readonly ExcludedProduct[]
}) {
  const [expanded, setExpanded] = useState(false)
  const factsOpen = expanded || showEvidence
  const factsId = useId()
  const counts = countLevels(line)
  const requirements = item?.requirements ?? []
  // The same requirement facts the gateway policy decided on and the decision footer reads (T41).
  const checks = requirementChecks(line, item, forbiddenAllergens)

  const subFact = line.facts.find((f) => f.fact === 'substitute.valio')
  const regularFacts = showEvidence ? line.facts : line.facts.filter((f) => f.fact !== 'substitute.valio')

  const syntheticContentFact: TrustLabel = {
    fact: 'content.perSalesUnit',
    value: line.contentPerSalesUnit.value,
    display: `${formatQuantity(line.contentPerSalesUnit.value, line.contentPerSalesUnit.unit)} / ${line.salesUnitLabel}`,
    level: line.contentPerSalesUnit.level,
    source: {
      system: 'valio-aimo.product-data',
      field: 'unitConversions',
      retrievedAt: null,
    },
    method: line.contentPerSalesUnit.method ?? null,
    conflict: null,
  }

  const hasContentFact = regularFacts.some((f) => f.fact === 'content.perSalesUnit')
  const displayedFacts = hasContentFact ? regularFacts : [syntheticContentFact, ...regularFacts]
  const ToggleIcon = expanded ? ChevronUp : ChevronDown

  const live = liveCheckPhrase(line, mode)
  const conflicts = line.facts.filter((f) => f.conflict).length
  const summaryParts: Array<{ key: string; node: ReactNode }> = [
    ...checks.map((check) => ({
      key: `req-${check.requirement.kind}-${check.requirement.code}`,
      node: (
        <span
          data-requirement={`${check.requirement.kind}:${check.requirement.code}`}
          data-proven={check.proven ? 'true' : 'false'}
          title={
            check.fact
              ? `Source: ${sourceShort(check.fact)} · ${sourceText(check.fact)}`
              : 'No evidence on this line'
          }
          className={check.proven ? 'font-semibold' : 'font-semibold text-[var(--block)]'}
        >
          {requirementPhrase(check)}
        </span>
      ),
    })),
    ...(live ? [{ key: 'live', node: <span>{live}</span> }] : []),
    ...(conflicts > 0
      ? [
          {
            key: 'conflict',
            node: (
              <span className="font-semibold text-[var(--block)]">
                {conflicts === 1 ? '1 fact disagrees' : `${conflicts} facts disagree`} between
                sources: see all facts
              </span>
            ),
          },
        ]
      : []),
  ]
  const substituteWhy = requestedDate
    ? substituteWhySentence(line, item, { requestedDate, excluded, forbiddenAllergens })
    : null

  return (
    <article
      className="mt-2 border-t border-[var(--line)] pt-2.5"
      aria-label={`Trust card for ${line.name}`}
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <h3 className="text-xs font-bold text-[var(--ink)]" title={line.name}>
            Trust card · {line.productId}
          </h3>
          {/* T109: one plain line for the buyer; levels, sources and conflict codes stay in the full fact list. */}
          {summaryParts.length > 0 && (
            <p className="mt-0.5 text-xs text-[var(--ink)]" data-testid="evidence-summary">
              {summaryParts.map((part, idx) => (
                <span key={part.key}>
                  {idx > 0 && ' · '}
                  {part.node}
                </span>
              ))}
            </p>
          )}
          {substituteWhy && (
            <p className="mt-0.5 text-xs text-[var(--ink)]" data-testid="substitute-why">
              <ProposalRefText text={substituteWhy} />
            </p>
          )}
        </div>
        {!showEvidence && (
          <button
            type="button"
            aria-expanded={expanded}
            aria-controls={factsId}
            onClick={() => setExpanded((prev) => !prev)}
            className="disclosure-btn"
          >
            <ToggleIcon size={13} aria-hidden />
            <span>{expanded ? 'Hide facts' : `Show all facts (${displayedFacts.length})`}</span>
          </button>
        )}
      </div>

      {factsOpen && (
        <div id={factsId}>
          <ul
            className="mt-2 flex flex-wrap items-center gap-2 text-xs font-semibold"
            aria-label={`Trust summary for ${line.name}`}
          >
            <li className="text-[var(--auto)]">{counts.VERIFIED} Verified</li>
            <li className="text-[var(--info)]">{counts.DECLARED} Declared</li>
            <li className="text-[var(--ask)]">{counts.ESTIMATE} Estimate</li>
            <li className="text-[var(--muted)]">{counts.UNKNOWN} Unknown</li>
            {counts.CONFLICT > 0 && (
              <li className="text-[var(--block)]">{counts.CONFLICT} Conflict</li>
            )}
          </ul>
          <dl className="mt-2 grid border-t border-[var(--line)]">
            {displayedFacts.map((fact, idx) => (
              <div
                key={`${fact.fact}-${idx}`}
                data-fact={fact.fact}
                className="grid grid-cols-1 items-baseline gap-1 border-b border-[var(--line)] py-1.5 text-sm last:border-b-0 sm:grid-cols-[160px_minmax(0,1fr)_auto]"
              >
                <dt className="text-xs font-semibold text-[var(--muted)]">
                  {factLabel(fact.fact)}
                </dt>
                <dd className="min-w-0">
                  <div className="font-semibold text-[var(--ink)]">
                    <ProposalRefText text={formatFactDisplay(fact)} />
                  </div>
                  <TrustSourceMeta fact={fact} />
                  {fact.conflict && (
                    <p className="mt-1 flex items-center gap-1 text-xs font-semibold text-[var(--block)]">
                      <AlertTriangle size={12} aria-hidden />
                      <span>{fact.conflict.message}</span>
                    </p>
                  )}
                </dd>
                <div className="sm:justify-self-end">
                  <TrustBadge level={fact.level} conflict={fact.conflict} />
                </div>
              </div>
            ))}
          </dl>
        </div>
      )}

      {(subFact || excludedSubstitutes.length > 0) && (
        <div
          className="mt-2 border-t border-[var(--line)] pt-2 text-xs text-[var(--ink)]"
          role="note"
          aria-label="Valio-substitute check"
        >
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="inline-flex items-center gap-1.5 font-bold text-[var(--block)]">
              <ShieldAlert size={14} aria-hidden />
              <span>Substitute check</span>
            </div>
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="font-semibold text-[var(--block)]">
                {reasonChip('SUBSTITUTE_REJECTED')}
              </span>
              {subFact && <TrustBadge level={subFact.level} conflict={subFact.conflict} />}
            </div>
          </div>
          {excludedSubstitutes.length > 0 ? (
            excludedSubstitutes.map((sub) => {
              const reqNames =
                requirements.map((r) => requirementLabel(r).toLowerCase()).join(', ') ||
                'hard requirements'
              return (
                <p key={sub.productId} className="mt-1">
                  Valio lists <strong>{sub.productId}</strong> ({sub.name}) as the substitute; it
                  fails &lsquo;{reqNames}&rsquo;, so the agent never swaps to it.
                </p>
              )
            })
          ) : (
            <p className="mt-1">{subFact ? formatFactDisplay(subFact) : null}</p>
          )}
        </div>
      )}
    </article>
  )
}
