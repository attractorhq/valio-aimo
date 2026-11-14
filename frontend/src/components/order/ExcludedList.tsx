import { useId, useState } from 'react'
import { Ban, ShieldAlert } from 'lucide-react'
import type { ExcludedProduct, Proposal, ReasonCode, RequestItem } from '../../lib/contracts'
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

function isValioSubstitute(entry: ExcludedProduct): boolean {
  return Boolean(entry.substituteFor) || entry.reasons.includes('SUBSTITUTE_REJECTED')
}

/** "22 excluded · 15 not gluten-free, 7 allergen unknown" (each candidate counted by its first reason). */
export function excludedSummary(entries: ExcludedProduct[], item?: RequestItem): string {
  if (entries.length === 0) return 'No candidates excluded'
  const counts = new Map<string, number>()
  for (const entry of entries) {
    const first = entry.reasons[0]
    const label = first ? formatExcludedReasonChip(first, item).toLowerCase() : 'excluded'
    counts.set(label, (counts.get(label) ?? 0) + 1)
  }
  const parts = [...counts.entries()]
    .sort((a, b) => b[1] - a[1])
    .map(([label, n]) => `${n} ${label}`)
  return `${entries.length} excluded · ${parts.join(', ')}`
}

function ExcludedEntry({ entry, item }: { entry: ExcludedProduct; item?: RequestItem }) {
  const [open, setOpen] = useState(false)
  const factsId = useId()
  const valioSub = isValioSubstitute(entry)
  // Name only requirements the request actually carries; never assume one.
  const reqNames =
    item?.requirements?.map((r) => requirementLabel(r).toLowerCase()).join(', ') ||
    'a hard requirement'
  const facts = entry.facts ?? []

  return (
    <li
      className={`rounded-[12px] border px-2.5 py-1.5 text-xs ${
        valioSub
          ? 'border-[var(--block)] bg-[var(--block-bg)]'
          : 'border-[var(--line)] bg-[var(--surface)]'
      }`}
    >
      <div className="flex flex-wrap items-center justify-between gap-1.5">
        <div className="min-w-0">
          <span className="font-semibold text-[var(--ink)]">{entry.name}</span>{' '}
          <span className="text-[var(--muted)]">· {entry.productId}</span>
          {item && <span className="ml-1 text-[var(--muted)]">({item.what})</span>}
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
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
          {facts.length > 0 && (
            <button
              type="button"
              className="disclosure-btn"
              aria-expanded={open}
              aria-controls={factsId}
              onClick={() => setOpen((v) => !v)}
            >
              {open ? 'Hide evidence' : `Show evidence (${facts.length})`}
            </button>
          )}
        </div>
      </div>
      {valioSub && (
        <p className="mt-0.5 flex items-center gap-1.5 font-bold text-[var(--block)]">
          <ShieldAlert size={13} aria-hidden />
          <span>
            Valio Aimo lists {entry.productId} ({entry.name}) as the substitute for{' '}
            {entry.substituteFor ?? 'this line'}. It fails &lsquo;{reqNames}&rsquo; for this buyer,
            so the agent will never swap to it.
          </span>
        </p>
      )}
      {open && (
        <ul id={factsId} className="mt-1 grid gap-1 border-t border-[var(--line)] pt-1">
          {facts.map((fact, idx) => (
            <li
              key={`${fact.fact}-${idx}`}
              data-fact={fact.fact}
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
}

/**
 * Step 2 Lookup: one line per item with its excluded-candidate summary (T43: lead with the result).
 * Rejected Valio-listed substitutes stay visible (P8 B); every other excluded candidate, and every
 * candidate's evidence, is one click away.
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
  const [showAll, setShowAll] = useState(false)
  const listId = useId()
  const request = directRequest ?? proposal?.request
  const excluded = directExcluded ?? proposal?.excluded ?? []
  const lines = proposal?.lines ?? []
  const itemsById = new Map((request?.items ?? []).map((item) => [item.itemId, item]))
  const plansByItemId = new Map((plan ?? []).map((p) => [p.item.itemId, p]))
  const substitutes = excluded.filter(isValioSubstitute)
  const others = excluded.filter((e) => !isValioSubstitute(e))
  const itemOf = (e: ExcludedProduct) => (e.itemId ? itemsById.get(e.itemId) : undefined)

  return (
    <div className="grid gap-3">
      {request && (
        <ul className="lookup-list">
          {request.items.map((item) => {
            const chosen = lines.filter((l) => l.itemId === item.itemId)
            const itemExcluded = excluded.filter((e) => e.itemId === item.itemId)
            const shortlistIds = plansByItemId.get(item.itemId)?.shortlistIds ?? []
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
                    <small
                      className="muted ml-1.5"
                      data-shortlist={shortlistIds.join(', ')}
                      title={`Shortlist: ${shortlistIds.join(', ')}`}
                    >
                      {shortlistIds.length} shortlisted
                    </small>
                  )}
                </span>
                <span className="muted">{excludedSummary(itemExcluded, item)}</span>
              </li>
            )
          })}
        </ul>
      )}

      {excluded.length > 0 && (
        <section
          className="min-w-0 rounded-[24px] border border-[var(--line)] bg-[var(--tint)] px-3.5 py-2.5 [overflow-wrap:break-word]"
          aria-label="Excluded candidates"
        >
          <header className="flex flex-wrap items-center justify-between gap-2">
            <h3
              className="text-xs font-bold tracking-wider text-[var(--muted)] uppercase"
              title="Rejected by hard rules. Never offered for approval."
            >
              {excluded.length} excluded
              <span className="visually-hidden">
                {' '}
                · rejected by hard rules, never offered for approval
              </span>
            </h3>
            {others.length > 0 && (
              <button
                type="button"
                className="disclosure-btn"
                aria-expanded={showAll}
                aria-controls={listId}
                onClick={() => setShowAll((v) => !v)}
              >
                {showAll
                  ? 'Hide excluded candidates'
                  : `Show excluded candidates (${others.length})`}
              </button>
            )}
          </header>
          {substitutes.length > 0 && (
            <ul className="mt-1.5 grid gap-1.5" aria-label="Rejected Valio-listed substitutes">
              {substitutes.map((entry) => (
                <ExcludedEntry
                  key={`${entry.productId}-${entry.itemId ?? 'all'}`}
                  entry={entry}
                  item={itemOf(entry)}
                />
              ))}
            </ul>
          )}
          {showAll && (
            <ul id={listId} className="mt-1.5 grid gap-1.5">
              {others.map((entry) => (
                <ExcludedEntry
                  key={`${entry.productId}-${entry.itemId ?? 'all'}`}
                  entry={entry}
                  item={itemOf(entry)}
                />
              ))}
            </ul>
          )}
        </section>
      )}
    </div>
  )
}
