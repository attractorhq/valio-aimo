import { Ban, ShieldAlert } from 'lucide-react'
import type { Proposal, ReasonCode, RequestItem } from '../../lib/contracts'
import { reasonChip, requirementLabel } from '../../lib/format'
import type { ItemPlanSummary } from '../request/AssumptionChips'
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

/**
 * Step 2 Lookup + Excluded list with reason chips and Valio-listed-substitute check (P6 P0-3, P8 B).
 */
export function ExcludedList({
  proposal,
  request: directRequest,
  excluded: directExcluded,
  plan,
}: {
  proposal?: Proposal
  request?: Proposal['request']
  excluded?: Proposal['excluded']
  plan?: ItemPlanSummary[]
}) {
  const request = directRequest ?? proposal?.request
  const excluded = directExcluded ?? proposal?.excluded ?? []
  const lines = proposal?.lines ?? []
  const itemsById = new Map((request?.items ?? []).map((item) => [item.itemId, item]))
  const plansByItemId = new Map((plan ?? []).map((p) => [p.item.itemId, p]))

  return (
    <div className="grid gap-3">
      {request && (
        <ul className="lookup-list">
          {request.items.map((item) => {
            const chosen = lines.filter((l) => l.itemId === item.itemId)
            const out = excluded.filter((e) => e.itemId === item.itemId).length
            const itemPlan = plansByItemId.get(item.itemId)
            const shortlistIds = itemPlan?.shortlistIds ?? []
            return (
              <li key={item.itemId}>
                <strong>{item.what}</strong>
                <span>
                  {chosen.length
                    ? chosen.map((l) => l.name).join(' + ')
                    : proposal
                      ? 'No compliant product'
                      : 'Shortlist evaluated'}
                  {shortlistIds.length > 0 && (
                    <small className="muted ml-1.5">
                      Shortlist ({shortlistIds.length}): {shortlistIds.join(', ')}
                    </small>
                  )}
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
      )}

      {excluded.length > 0 && (
        <section
          className="min-w-0 rounded-xl border border-[var(--line)] bg-[var(--bg)] px-2.5 py-2 [overflow-wrap:anywhere]"
          aria-label="Excluded candidates"
        >
          <header className="mb-1 flex flex-wrap items-center justify-between gap-2">
            <h3 className="text-xs font-bold tracking-wider text-[var(--muted)] uppercase">
              Excluded ({excluded.length}) · rejected by hard rules, never offered for approval
            </h3>
          </header>
          <ul className="flex flex-wrap gap-1.5">
            {excluded.map((entry) => {
              const item = entry.itemId ? itemsById.get(entry.itemId) : undefined
              const isValioSub =
                Boolean(entry.substituteFor) || entry.reasons.includes('SUBSTITUTE_REJECTED')
              // Name only requirements the request actually carries; never assume one.
              const reqNames =
                item?.requirements?.map((r) => requirementLabel(r).toLowerCase()).join(', ') ||
                'a hard requirement'

              return (
                <li
                  key={`${entry.productId}-${entry.itemId ?? 'all'}`}
                  className={`rounded-lg border px-2 py-1 text-xs ${
                    isValioSub
                      ? 'w-full border-[var(--block)] bg-[var(--block-bg)]'
                      : 'inline-flex flex-wrap items-center gap-1.5 border-[var(--line)] bg-[var(--surface)]'
                  }`}
                >
                  <div className="flex flex-wrap items-center justify-between gap-1.5">
                    <div className="min-w-0">
                      <span className="font-semibold text-[var(--ink)]">{entry.name}</span>{' '}
                      <span className="text-[var(--muted)]">· {entry.productId}</span>
                      {item && <span className="ml-1 text-[var(--muted)]">({item.what})</span>}
                    </div>
                    <ul className="chip-list" aria-label={`Exclusion reasons for ${entry.name}`}>
                      {entry.reasons.map((code) => (
                        <li
                          key={code}
                          className="chip border-[var(--block)] bg-[var(--surface)] text-[var(--block)]"
                        >
                          <Ban size={11} aria-hidden />
                          <span>{formatExcludedReasonChip(code, item)}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                  {isValioSub && (
                    <p className="mt-0.5 flex items-center gap-1.5 font-bold text-[var(--block)]">
                      <ShieldAlert size={13} aria-hidden />
                      <span>
                        Valio Aimo lists {entry.productId} ({entry.name}) as the substitute for{' '}
                        {entry.substituteFor ?? 'this line'}. It fails &lsquo;{reqNames}&rsquo; for
                        this buyer, so the agent will never swap to it.
                      </span>
                    </p>
                  )}
                  {entry.facts && entry.facts.length > 0 && (
                    <ul className="mt-1 grid gap-1 border-t border-[var(--line)] pt-1">
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
