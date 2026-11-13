import { Ban, ShieldAlert } from 'lucide-react'
import type { ExcludedProduct, Proposal, ReasonCode, RequestItem } from '../../lib/contracts'
import { formatDate, reasonChip, reasonText, requirementLabel } from '../../lib/format'
import { TrustBadge, TrustSourceMeta, formatFactDisplay } from '../trust/TrustBadge'

function formatExcludedReasonChip(code: ReasonCode, item?: RequestItem): string {
  if (code === 'CLAIM_MISSING' && item?.requirements?.length) {
    const claimReq = item.requirements.find((r) => r.kind === 'CLAIM')
    if (claimReq) {
      return `Not ${requirementLabel(claimReq).toLowerCase()}`
    }
  }
  return reasonChip(code)
}

function formatExcludedReasonExplanation(
  code: ReasonCode,
  excluded: ExcludedProduct,
  proposal: Proposal,
  item?: RequestItem,
): string {
  const claimReq = item?.requirements?.find((r) => r.kind === 'CLAIM')
  const reqText = claimReq
    ? requirementLabel(claimReq).toLowerCase()
    : item?.requirements?.[0]
      ? requirementLabel(item.requirements[0]).toLowerCase()
      : 'hard requirement'
  const cutoffFact = excluded.facts?.find((f) => f.fact === 'delivery.cutoff')
  const dateFact = excluded.facts?.find((f) => f.fact === 'delivery.date')
  const params: Record<string, string> = {
    claim: reqText,
    requirement: reqText,
    product: `${excluded.productId} (${excluded.name})`,
    original: excluded.substituteFor ?? 'requested product',
    closingTime: cutoffFact?.display ?? 'already elapsed',
    availableDate: dateFact?.display ?? formatDate(proposal.requestedDate),
    detail: 'Conflicting allergen and nutritional claim declarations from the supplier.',
  }
  const rendered = reasonText(code, params)
  return /\{\w+\}/.test(rendered) ? formatExcludedReasonChip(code, item) : rendered
}

/**
 * Step 2 Lookup + Excluded list with reason chips and Valio-listed-substitute check (P6 P0-3, P8 B).
 */
export function ExcludedList({ proposal }: { proposal: Proposal }) {
  const excluded = proposal.excluded ?? []
  const itemsById = new Map(proposal.request.items.map((item) => [item.itemId, item]))

  return (
    <div className="grid gap-3">
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

      {excluded.length > 0 && (
        <section
          className="rounded-xl border border-[var(--line)] bg-[var(--bg)] p-3.5"
          aria-label="Excluded candidates"
        >
          <header className="mb-2.5 flex flex-wrap items-center justify-between gap-2">
            <h3 className="text-xs font-bold tracking-wider text-[var(--muted)] uppercase">
              Excluded ({excluded.length}) · rejected by hard rules, never offered for approval
            </h3>
          </header>
          <ul className="grid gap-2.5">
            {excluded.map((entry) => {
              const item = entry.itemId ? itemsById.get(entry.itemId) : undefined
              const isValioSub =
                Boolean(entry.substituteFor) || entry.reasons.includes('SUBSTITUTE_REJECTED')
              const reqNames =
                item?.requirements?.map((r) => requirementLabel(r).toLowerCase()).join(', ') ||
                'gluten-free'

              return (
                <li
                  key={`${entry.productId}-${entry.itemId ?? 'all'}`}
                  className={`rounded-lg border p-3 text-sm ${
                    isValioSub
                      ? 'border-[var(--block)] bg-[var(--block-bg)]'
                      : 'border-[var(--line)] bg-[var(--surface)]'
                  }`}
                >
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div>
                      <div className="font-semibold text-[var(--ink)]">
                        {entry.name}{' '}
                        <span className="font-normal text-[var(--muted)]">
                          · {entry.productId}
                        </span>
                        {item && (
                          <span className="ml-1.5 text-xs font-normal text-[var(--muted)]">
                            (for {item.what})
                          </span>
                        )}
                      </div>
                      {isValioSub && (
                        <p className="mt-1 flex items-center gap-1.5 text-xs font-bold text-[var(--block)]">
                          <ShieldAlert size={14} aria-hidden />
                          <span>
                            Valio Aimo lists {entry.productId} ({entry.name}) as the substitute for{' '}
                            {entry.substituteFor ?? 'this line'}. It fails &lsquo;{reqNames}&rsquo;
                            for this buyer, so the agent will never swap to it.
                          </span>
                        </p>
                      )}
                    </div>
                    <ul
                      className="chip-list"
                      aria-label={`Exclusion reasons for ${entry.name}`}
                    >
                      {entry.reasons.map((code) => (
                        <li
                          key={code}
                          className="chip border-[var(--block)] bg-[var(--surface)] text-[var(--block)]"
                        >
                          <Ban size={12} aria-hidden />
                          <span>{formatExcludedReasonChip(code, item)}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                  <p className="mt-1.5 text-xs text-[var(--muted)]">
                    {entry.reasons
                      .map((code) => formatExcludedReasonExplanation(code, entry, proposal, item))
                      .join(' · ')}
                  </p>
                  {entry.facts && entry.facts.length > 0 && (
                    <ul className="mt-2 grid gap-1 border-t border-[var(--line)] pt-2">
                      {entry.facts.map((fact, idx) => (
                        <li
                          key={`${fact.fact}-${idx}`}
                          className="flex flex-wrap items-center justify-between gap-2 text-xs"
                        >
                          <span>
                            <strong>{fact.fact}:</strong> {formatFactDisplay(fact)}
                          </span>
                          <TrustSourceMeta fact={fact} />
                          <TrustBadge level={fact.level} conflict={fact.conflict} />
                        </li>
                      ))}
                    </ul>
                  )}
                </li>
              )
            })}
          </ul>
        </section>
      )}
    </div>
  )
}
