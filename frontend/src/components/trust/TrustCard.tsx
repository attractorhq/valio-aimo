import { useId, useState } from 'react'
import { AlertTriangle, ChevronDown, ChevronUp, ShieldAlert } from 'lucide-react'
import type {
  ExcludedProduct,
  OfferLine,
  RequestItem,
  TrustLabel,
  TrustLevel,
} from '../../lib/contracts'
import { formatQuantity, reasonChip, requirementLabel } from '../../lib/format'
import { requirementChecks } from '../../lib/requirements'
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
  if (factName === 'delivery.date') return 'Delivery date'
  if (factName === 'delivery.cutoff') return 'Order cut-off'
  if (factName === 'content.perSalesUnit') return 'Pack content'
  if (factName === 'reliability') return 'Delivery reliability'
  if (factName === 'co2') return 'Carbon footprint'
  if (factName === 'origin') return 'Origin'
  if (factName === 'substitute.valio') return 'Valio substitute check'
  if (factName.startsWith('claim.')) return `Claim (${factName.slice(6)})`
  if (factName.startsWith('allergen.')) return `Allergen (${factName.slice(9)})`
  if (factName.startsWith('label.')) return `Label (${factName.slice(6)})`
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
}: {
  line: OfferLine
  item?: RequestItem
  /** The mandate's forbidden allergens: hard requirements the policy enforces on every line. */
  forbiddenAllergens?: readonly string[]
  excludedSubstitutes?: ExcludedProduct[]
}) {
  const [expanded, setExpanded] = useState(false)
  const factsId = useId()
  const counts = countLevels(line)
  const requirements = item?.requirements ?? []
  // The same requirement facts the gateway policy decided on and the decision footer reads (T41).
  const checks = requirementChecks(line, item, forbiddenAllergens)

  const subFact = line.facts.find((f) => f.fact === 'substitute.valio')
  const regularFacts = line.facts.filter((f) => f.fact !== 'substitute.valio')

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

  return (
    <article
      className="mt-2 rounded-xl border border-[var(--line)] bg-[var(--bg)] px-3 py-2"
      aria-label={`Trust card for ${line.name}`}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <h3 className="text-xs font-bold text-[var(--ink)]">
            Trust card · {line.name}{' '}
            <span className="font-normal text-[var(--muted)]">({line.productId})</span>
          </h3>
          {checks.map(({ requirement: req, fact, fromMandate, proven }) => (
            <span
              key={`${req.kind}-${req.code}`}
              data-requirement={`${req.kind}:${req.code}`}
              data-proven={proven ? 'true' : 'false'}
              title={fact ? `Source: ${sourceText(fact)}` : 'No evidence on this line'}
              className="inline-flex flex-wrap items-center gap-1 rounded-md border border-[var(--line)] bg-[var(--surface)] px-2 py-0.5 text-xs"
            >
              <strong className="text-[var(--ink)]">
                {requirementLabel(req)}
                {fromMandate ? ' (mandate)' : ''}:
              </strong>
              <span>{formatFactDisplay(fact)}</span>
              <TrustBadge level={fact?.level ?? 'UNKNOWN'} conflict={fact?.conflict} />
              {fact && <span className="text-[var(--muted)]">{sourceShort(fact)}</span>}
            </span>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <ul
            className="flex flex-wrap items-center gap-1 text-xs font-semibold"
            aria-label={`Trust summary for ${line.name}`}
          >
            <li className="rounded-md bg-[var(--auto-bg)] px-1.5 py-0.5 text-[var(--auto)]">
              {counts.VERIFIED} Verified
            </li>
            <li className="rounded-md bg-[var(--info-bg)] px-1.5 py-0.5 text-[var(--info)]">
              {counts.DECLARED} Declared
            </li>
            <li className="rounded-md bg-[var(--ask-bg)] px-1.5 py-0.5 text-[var(--ask)]">
              {counts.ESTIMATE} Estimate
            </li>
            <li className="rounded-md border border-dashed border-[var(--muted)] bg-[var(--surface)] px-1.5 py-0.5 text-[var(--muted)]">
              {counts.UNKNOWN} Unknown
            </li>
            {counts.CONFLICT > 0 && (
              <li className="rounded-md bg-[var(--block-bg)] px-1.5 py-0.5 text-[var(--block)]">
                {counts.CONFLICT} Conflict
              </li>
            )}
          </ul>
          <button
            type="button"
            aria-expanded={expanded}
            aria-controls={factsId}
            onClick={() => setExpanded((prev) => !prev)}
            className="inline-flex items-center gap-1 rounded-lg border border-[var(--line)] bg-[var(--surface)] px-2 py-0.5 text-xs font-semibold text-[var(--ink)] hover:border-[var(--green)]"
          >
            <ToggleIcon size={13} aria-hidden />
            <span>{expanded ? 'Hide facts' : `Show facts (${displayedFacts.length})`}</span>
          </button>
        </div>
      </div>

      {expanded && (
        <dl id={factsId} className="mt-2 grid gap-1.5 border-t border-[var(--line)] pt-2">
          {displayedFacts.map((fact, idx) => (
            <div
              key={`${fact.fact}-${idx}`}
              className="grid grid-cols-1 items-baseline gap-1 rounded-lg bg-[var(--surface)] px-3 py-1.5 text-sm sm:grid-cols-[160px_minmax(0,1fr)_auto]"
            >
              <dt className="text-xs font-semibold text-[var(--muted)]">{factLabel(fact.fact)}</dt>
              <dd className="min-w-0">
                <div className="font-semibold text-[var(--ink)]">{formatFactDisplay(fact)}</div>
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
      )}

      {(subFact || excludedSubstitutes.length > 0) && (
        <div
          className="mt-2 rounded-lg border border-[var(--block)] bg-[var(--block-bg)] px-2.5 py-1.5 text-xs text-[var(--ink)]"
          role="note"
          aria-label="Valio-substitute check"
        >
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="inline-flex items-center gap-1.5 font-bold text-[var(--block)]">
              <ShieldAlert size={14} aria-hidden />
              <span>Valio Aimo substitute check</span>
            </div>
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="chip border-[var(--block)] bg-[var(--surface)] text-[var(--block)]">
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
                  Valio Aimo lists <strong>{sub.productId}</strong> ({sub.name}) as the substitute
                  for <strong>{line.productId}</strong>. It fails &lsquo;{reqNames}&rsquo; for this
                  buyer, so the agent will never swap to it.
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
