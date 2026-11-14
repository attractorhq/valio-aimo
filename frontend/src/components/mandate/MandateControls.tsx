import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Lock, RotateCcw, SlidersHorizontal } from 'lucide-react'
import { Link } from 'react-router-dom'
import type { Mandate } from '../../lib/contracts'
import { formatMoney } from '../../lib/format'
import { gateway, subscribeGatewaySource, type DataSource } from '../../lib/gateway'
import { MandatePanel } from '../flow/MandatePanel'
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

const SHIPPED_DEFAULTS: Record<
  string,
  Pick<Mandate, 'autoApproveLimitEur' | 'hardLimitEur' | 'allergenMode'>
> = {
  'nordic-harbour-hotels': {
    autoApproveLimitEur: '1500.00',
    hardLimitEur: '5000.00',
    allergenMode: 'STANDARD',
  },
  'mock-hotels': {
    autoApproveLimitEur: '1500.00',
    hardLimitEur: '5000.00',
    allergenMode: 'STANDARD',
  },
  'northbay-school-kitchen': {
    autoApproveLimitEur: '500.00',
    hardLimitEur: '3000.00',
    allergenMode: 'STRICT',
  },
  'mock-school': {
    autoApproveLimitEur: '500.00',
    hardLimitEur: '3000.00',
    allergenMode: 'STRICT',
  },
}

function fallbackDifferences(mandate: Mandate): MandateDifference[] {
  const shipped = SHIPPED_DEFAULTS[mandate.customerId]
  if (!shipped) return []
  const diffs: MandateDifference[] = []
  if (mandate.autoApproveLimitEur !== shipped.autoApproveLimitEur) {
    diffs.push({
      field: 'autoApproveLimitEur',
      current: mandate.autoApproveLimitEur,
      default: shipped.autoApproveLimitEur,
    })
  }
  if (mandate.hardLimitEur !== shipped.hardLimitEur) {
    diffs.push({
      field: 'hardLimitEur',
      current: mandate.hardLimitEur,
      default: shipped.hardLimitEur,
    })
  }
  if (mandate.allergenMode !== shipped.allergenMode) {
    diffs.push({
      field: 'allergenMode',
      current: mandate.allergenMode,
      default: shipped.allergenMode,
    })
  }
  return diffs
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
 * T46: Read-only mandate version and "changed from default" notice on the buyer page.
 * The buyer page has no mandate write controls; changes happen only on `/admin`.
 */
export function MandateStatus({ mandate, live }: { mandate: Mandate; live: boolean }) {
  const defaults = useQuery({
    queryKey: ['mandate-default', mandate.customerId, mandate.version],
    queryFn: () => getMandateDefault(mandate.customerId),
    enabled: live,
    retry: false,
  })
  const differences = defaults.data?.differences ?? fallbackDifferences(mandate)
  const changed = (defaults.data?.changedFromDefault ?? false) || differences.length > 0

  return (
    <section className="panel mt-3" aria-labelledby="mandate-status-heading">
      <h2 id="mandate-status-heading" className="panel-title inline-flex items-center gap-2">
        <Lock size={14} aria-hidden /> Mandate v{mandate.version} · read-only
      </h2>
      {changed && differences.length > 0 ? (
        <p className="notice small mt-1" role="note">
          {changedFromDefaultText(differences)}
        </p>
      ) : (
        <p className="muted small">Unchanged from default mandate.</p>
      )}
      <p className="small mt-2">
        <Link to="/admin" className="font-semibold text-[var(--green)] underline">
          Buyer admin (demo, no login)
        </Link>
      </p>
    </section>
  )
}

/**
 * D5 buyer-admin controls (on `/admin`): change the auto-approve limit or the allergen mode, and the gateway
 * re-decides open proposals at once with its deterministic policy (no model call). Every change is audited and
 * bumps the mandate version. A one-tap reset restores the shipped default as a new version (T36), so a shared
 * demo can always return to the three-outcome story. Needs the live gateway.
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
    queryClient.invalidateQueries({ queryKey: ['mandates'] })
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
  const differences = defaults.data?.differences ?? fallbackDifferences(mandate)
  const changed = (defaults.data?.changedFromDefault ?? false) || differences.length > 0

  return (
    <section className="panel" aria-labelledby="mandate-controls-heading">
      <h2 id="mandate-controls-heading" className="panel-title inline-flex items-center gap-2">
        <SlidersHorizontal size={16} aria-hidden /> Buyer admin controls
      </h2>
      <p className="muted small">
        Policy is changed by a human buyer admin, never by the agent. Open proposals are re-decided
        immediately, and the change is logged.
      </p>
      {live && changed && differences.length > 0 && (
        <div className="notice small mt-2" role="note">
          <p>{changedFromDefaultText(differences)}</p>
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
          className="rounded-[12px] border border-[var(--line-strong)] bg-[var(--surface)] px-3 py-1.5"
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
    </section>
  )
}

/**
 * T46 `/admin` view: buyer-admin mandate controls moved off the buyer page and explicitly labelled
 * "Buyer admin (demo, no login)".
 */
export function BuyerAdmin() {
  const mandates = useQuery({ queryKey: ['mandates'], queryFn: () => gateway.listMandates() })
  useQuery({
    queryKey: ['gateway-health'],
    queryFn: () => gateway.checkHealth(),
    retry: false,
  })
  const [customerId, setCustomerId] = useState<string>()
  const [sourceState, setSourceState] = useState<DataSource>(() => gateway.source)

  useEffect(() => {
    return subscribeGatewaySource((next) => setSourceState(next))
  }, [])

  useEffect(() => {
    if (!customerId && mandates.data?.length) setCustomerId(mandates.data[0].customerId)
  }, [customerId, mandates.data])

  const mandate = mandates.data?.find((m) => m.customerId === customerId)
  const live = sourceState === 'GATEWAY' || gateway.source === 'GATEWAY'

  return (
    <div className="approver-page">
      <header className="panel flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold">Buyer admin (demo, no login)</h1>
          <p className="muted small mt-1">
            Demo buyer-admin role — no authentication; production uses SSO/OIDC. Change or reset a
            buyer mandate here; the buyer view stays read-only.
          </p>
        </div>
        <Link to="/" className="font-semibold text-[var(--green)] underline">
          Back to the buyer view
        </Link>
      </header>
      <div className="main-grid">
        <aside className="main-side">
          {mandates.error ? (
            <p className="notice notice-error" role="alert">
              Could not load mandates.
            </p>
          ) : (
            <MandatePanel
              mandates={mandates.data ?? []}
              selected={mandate}
              onSelect={setCustomerId}
            />
          )}
        </aside>
        <div className="main-flow">
          {mandate && (
            <MandateControls
              mandate={mandate}
              live={live}
              onChanged={() => {
                mandates.refetch()
              }}
            />
          )}
        </div>
      </div>
    </div>
  )
}
