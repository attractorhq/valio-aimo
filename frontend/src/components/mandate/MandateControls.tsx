import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { RotateCcw, SlidersHorizontal } from 'lucide-react'
import type { Mandate } from '../../lib/contracts'
import { formatMoney } from '../../lib/format'
import {
  changeMandate,
  getMandateDefault,
  resetMandate,
  toMoneyString,
  type MandateChangeResult,
  type MandateDifference,
} from './adminApi'

const DECISION_WORDS = { AUTO: 'Auto', ASK: 'Ask', BLOCK: 'Block' } as const

const FIELD_LABELS: Record<string, string> = {
  autoApproveLimitEur: 'auto-approve',
  hardLimitEur: 'hard limit',
  allergenMode: 'allergen mode',
  autoAcceptValioSubstitutes: 'auto-accept Valio substitutes',
  autoAcceptSupplierStock: 'auto-accept supplier stock',
  minReliability: 'minimum reliability',
  enforceCutoff: 'enforce cut-off',
  forbiddenAllergens: 'forbidden allergens',
}

function showValue(field: string, value: unknown): string {
  if (field.endsWith('Eur') && typeof value === 'string') return formatMoney(value)
  if (field === 'allergenMode' && typeof value === 'string') {
    return value === 'STRICT' ? 'Strict' : 'Standard'
  }
  if (typeof value === 'boolean') return value ? 'yes' : 'no'
  if (Array.isArray(value)) return value.length ? value.join(', ') : 'none'
  return String(value)
}

/** "Changed from default: auto-approve €510.00 (default €500.00)" */
export function changedFromDefaultText(differences: MandateDifference[]): string {
  const parts = differences.map(
    (d) =>
      `${FIELD_LABELS[d.field] ?? d.field} ${showValue(d.field, d.current)} (default ${showValue(d.field, d.default)})`,
  )
  return `Changed from default: ${parts.join(' · ')}`
}

/**
 * D5 buyer-admin controls: change the auto-approve limit or the allergen mode, and the gateway re-decides
 * open proposals at once with its deterministic policy (no model call). Every change is audited and bumps
 * the mandate version. A one-tap reset restores the shipped default as a new version (T36), so a shared
 * demo can always return to the three-outcome story. Needs the live gateway.
 */
export function MandateControls({
  mandate,
  live,
  onChanged,
  defaultOpen = true,
}: {
  mandate: Mandate
  live: boolean
  onChanged?: (result: MandateChangeResult) => void
  /** Collapsed on phones so the request and the result come first (T43). */
  defaultOpen?: boolean
}) {
  const queryClient = useQueryClient()
  const [limit, setLimit] = useState(mandate.autoApproveLimitEur)
  const [mode, setMode] = useState(mandate.allergenMode)
  const [result, setResult] = useState<{ kind: 'change' | 'reset'; data: MandateChangeResult }>()
  useEffect(() => {
    setLimit(mandate.autoApproveLimitEur)
    setMode(mandate.allergenMode)
  }, [mandate.customerId, mandate.version, mandate.autoApproveLimitEur, mandate.allergenMode])

  const defaults = useQuery({
    queryKey: ['mandate-default', mandate.customerId, mandate.version],
    queryFn: () => getMandateDefault(mandate.customerId),
    enabled: live,
    retry: false,
  })

  function done(kind: 'change' | 'reset', data: MandateChangeResult) {
    setResult({ kind, data })
    queryClient.invalidateQueries({ queryKey: ['mandate-default', mandate.customerId] })
    onChanged?.(data)
  }

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
    onSuccess: (data) => done('change', data),
  })
  const reset = useMutation({
    mutationFn: () => resetMandate(mandate.customerId),
    onSuccess: (data) => done('reset', data),
  })
  const busy = apply.isPending || reset.isPending
  const error = apply.error ?? reset.error
  const changed = defaults.data?.changedFromDefault ?? false

  return (
    <section className="panel" aria-labelledby="mandate-controls-heading">
      <details className="admin-details" open={defaultOpen}>
        <summary className="admin-summary">
          <h2 id="mandate-controls-heading" className="panel-title inline-flex items-center gap-2">
            <SlidersHorizontal size={16} aria-hidden /> Buyer admin controls
          </h2>
        </summary>
        <p className="muted small">
          Policy is changed by a human buyer admin, never by the agent. Open proposals are
          re-decided immediately, and the change is logged.
        </p>
        {live && changed && defaults.data && (
          <div className="notice small mt-2" role="note">
            <p>{changedFromDefaultText(defaults.data.differences)}</p>
            <button
              type="button"
              className="btn mt-1 inline-flex items-center gap-1"
              onClick={() => reset.mutate()}
              disabled={busy}
            >
              <RotateCcw size={14} aria-hidden /> Reset to default mandate
            </button>
          </div>
        )}
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
            disabled={!live || busy}
            className="rounded-md border border-[var(--line)] px-2 py-1"
          />
          <fieldset disabled={!live || busy} className="flex gap-3">
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
          <button type="submit" className="btn btn-primary" disabled={!live || busy}>
            Apply policy change
          </button>
        </form>
        {!live && (
          <p className="notice small mt-2">
            Re-deciding needs the live gateway; the offline example proposals shown here are fixed.
          </p>
        )}
        {error && (
          <p className="notice notice-error small mt-2" role="alert">
            {error.message}
          </p>
        )}
        {result && (
          <div className="notice small mt-2" role="status">
            <p>
              {result.kind === 'reset'
                ? 'Policy reset to default by buyer admin'
                : 'Policy changed by buyer admin'}{' '}
              · mandate v{result.data.mandate.version} · auto-approve limit{' '}
              {formatMoney(result.data.mandate.autoApproveLimitEur)}
            </p>
            {result.data.redecided.length === 0 ? (
              <p>No open proposals to re-decide.</p>
            ) : (
              <ul>
                {result.data.redecided.map((proposal) => (
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
      </details>
    </section>
  )
}
