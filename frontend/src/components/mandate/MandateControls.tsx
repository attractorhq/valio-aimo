import { useEffect, useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { SlidersHorizontal } from 'lucide-react'
import type { Mandate } from '../../lib/contracts'
import { formatMoney } from '../../lib/format'
import { changeMandate, toMoneyString, type MandateChangeResult } from './adminApi'

const DECISION_WORDS = { AUTO: 'Auto', ASK: 'Ask', BLOCK: 'Block' } as const

/**
 * D5 buyer-admin controls: change the auto-approve limit or the allergen mode, and the gateway re-decides
 * open proposals at once with its deterministic policy (no model call). Every change is audited and bumps
 * the mandate version. Needs the live gateway: synthetic mock data cannot be re-decided.
 */
export function MandateControls({
  mandate,
  live,
  onChanged,
}: {
  mandate: Mandate
  live: boolean
  onChanged?: (result: MandateChangeResult) => void
}) {
  const [limit, setLimit] = useState(mandate.autoApproveLimitEur)
  const [mode, setMode] = useState(mandate.allergenMode)
  useEffect(() => {
    setLimit(mandate.autoApproveLimitEur)
    setMode(mandate.allergenMode)
  }, [mandate.customerId, mandate.version, mandate.autoApproveLimitEur, mandate.allergenMode])

  const apply = useMutation({
    mutationFn: () => {
      const amount = toMoneyString(limit)
      if (!amount)
        throw new Error('Enter the auto-approve limit as an amount in euros, e.g. 510.00.')
      const change: Parameters<typeof changeMandate>[1] = {}
      if (amount !== mandate.autoApproveLimitEur) change.autoApproveLimitEur = amount
      if (mode !== mandate.allergenMode) change.allergenMode = mode
      if (Object.keys(change).length === 0) throw new Error('Nothing to change.')
      return changeMandate(mandate.customerId, change)
    },
    onSuccess: (result) => onChanged?.(result),
  })
  const result = apply.data

  return (
    <section className="panel" aria-labelledby="mandate-controls-heading">
      <h2 id="mandate-controls-heading" className="panel-title inline-flex items-center gap-2">
        <SlidersHorizontal size={16} aria-hidden /> Buyer admin controls
      </h2>
      <p className="muted small">
        Policy is changed by a human buyer admin, never by the agent. Open proposals are re-decided
        immediately, and the change is logged.
      </p>
      <form
        className="mt-2 grid gap-2"
        onSubmit={(event) => {
          event.preventDefault()
          apply.mutate()
        }}
      >
        <label className="field-label" htmlFor="admin-limit">
          Auto-approve limit (€, excl. VAT)
        </label>
        <input
          id="admin-limit"
          inputMode="decimal"
          value={limit}
          onChange={(event) => setLimit(event.target.value)}
          disabled={!live || apply.isPending}
          className="rounded-md border border-[var(--line)] px-2 py-1"
        />
        <fieldset disabled={!live || apply.isPending} className="flex gap-3">
          <legend className="field-label">Allergen mode</legend>
          {(['STANDARD', 'STRICT'] as const).map((value) => (
            <label key={value} className="inline-flex items-center gap-1">
              <input
                type="radio"
                name="admin-allergen-mode"
                value={value}
                checked={mode === value}
                onChange={() => setMode(value)}
              />
              {value === 'STRICT' ? 'Strict' : 'Standard'}
            </label>
          ))}
        </fieldset>
        <button type="submit" className="btn btn-primary" disabled={!live || apply.isPending}>
          Apply policy change
        </button>
      </form>
      {!live && (
        <p className="notice small mt-2">
          Re-deciding needs the live gateway; the offline example proposals shown here are fixed.
        </p>
      )}
      {apply.error && (
        <p className="notice notice-error small mt-2" role="alert">
          {apply.error.message}
        </p>
      )}
      {result && (
        <div className="notice small mt-2" role="status">
          <p>
            Policy changed by buyer admin · mandate v{result.mandate.version} · auto-approve limit{' '}
            {formatMoney(result.mandate.autoApproveLimitEur)}
          </p>
          {result.redecided.length === 0 ? (
            <p>No open proposals to re-decide.</p>
          ) : (
            <ul>
              {result.redecided.map((proposal) => (
                <li key={proposal.proposalId}>
                  Re-decided {proposal.proposalId} (offer v{proposal.offerVersion}):{' '}
                  <strong>{DECISION_WORDS[proposal.decision]}</strong> ·{' '}
                  {formatMoney(proposal.totals.exclVat)}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  )
}
