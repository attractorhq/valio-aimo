import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Lock, RotateCcw, SlidersHorizontal } from 'lucide-react'
import { Link } from 'react-router-dom'
import type { Mandate } from '../../lib/contracts'
import { formatDate, formatMoney } from '../../lib/format'
import { gateway, subscribeGatewaySource, type DataSource } from '../../lib/gateway'
import { MandatePanel } from '../flow/MandatePanel'
import { LoadingSection, Skeleton } from '../Loading'
import {
  changeMandate,
  getMandateDefault,
  parseCategories,
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
  allowedCategories: 'allowed categories',
  validFrom: 'valid from',
  validTo: 'valid until',
  restaurantPreferences: 'restaurant preferences',
}

const SHIPPED_DEFAULTS: Record<
  string,
  Pick<Mandate, 'autoApproveLimitEur' | 'hardLimitEur' | 'allergenMode'>
> = {
  'nordic-harbour-hotels': {
    autoApproveLimitEur: '2500.00',
    hardLimitEur: '15000.00',
    allergenMode: 'STANDARD',
  },
  'mock-hotels': {
    autoApproveLimitEur: '2500.00',
    hardLimitEur: '15000.00',
    allergenMode: 'STANDARD',
  },
  'northbay-school-kitchen': {
    autoApproveLimitEur: '500.00',
    hardLimitEur: '6000.00',
    allergenMode: 'STRICT',
  },
  'mock-school': {
    autoApproveLimitEur: '500.00',
    hardLimitEur: '6000.00',
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
  if ((field === 'validFrom' || field === 'validTo') && typeof value === 'string') return formatDate(value)
  if (field === 'restaurantPreferences' && typeof value === 'string') {
    if (!value) return 'none'
    return value.length > 60 ? `“${value.slice(0, 60)}…”` : `“${value}”`
  }
  if (value === null || value === undefined) return 'none'
  if (typeof value === 'boolean') return value ? 'yes' : 'no'
  if (Array.isArray(value)) return value.length ? value.join(', ') : field === 'allowedCategories' ? 'any' : 'none'
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
    <section
      className="mandate-status"
      aria-labelledby="mandate-status-heading"
      aria-busy={live && defaults.isPending}
    >
      <div className="flex items-center justify-between gap-3">
        <h2 id="mandate-status-heading" className="panel-title inline-flex items-center gap-2">
          <Lock size={14} aria-hidden /> Mandate v{mandate.version} · read-only
        </h2>
        <Link to="/admin" className="small whitespace-nowrap font-semibold text-[var(--green)] underline">
          Buyer admin
        </Link>
      </div>
      {live && defaults.isPending && <Skeleton className="skeleton-status" />}
      {!(live && defaults.isPending) && changed && differences.length > 0 && (
        <p className="notice small mt-1" role="note">
          {changedFromDefaultText(differences)}
        </p>
      )}
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
  const [categories, setCategories] = useState((mandate.allowedCategories ?? []).join(', '))
  const [validFrom, setValidFrom] = useState(mandate.validFrom ?? '')
  const [validTo, setValidTo] = useState(mandate.validTo ?? '')
  const [result, setResult] = useState<{ kind: 'change' | 'reset'; data: MandateChangeResult }>()
  useEffect(() => {
    setLimit(mandate.autoApproveLimitEur)
    setMode(mandate.allergenMode)
    setCategories((mandate.allowedCategories ?? []).join(', '))
    setValidFrom(mandate.validFrom ?? '')
    setValidTo(mandate.validTo ?? '')
  }, [mandate])

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
      const allowed = parseCategories(categories)
      if (allowed.join(',') !== (mandate.allowedCategories ?? []).join(',')) change.allowedCategories = allowed
      if (validFrom !== (mandate.validFrom ?? '')) change.validFrom = validFrom || null
      if (validTo !== (mandate.validTo ?? '')) change.validTo = validTo || null
      if (validFrom && validTo && validTo < validFrom)
        throw new Error('The last valid day must not be before the first.')
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

  if (live && defaults.isPending)
    return <LoadingSection label="admin controls" variant="admin" className="panel" />

  return (
    <section className="panel" aria-labelledby="mandate-controls-heading">
      <h2 id="mandate-controls-heading" className="panel-title inline-flex items-center gap-2">
        <SlidersHorizontal size={16} aria-hidden /> Buyer admin controls
      </h2>
      <p className="muted small">Only a human admin changes it; open proposals are re-decided and logged.</p>
      {live && changed && differences.length > 0 && (
        <div className="notice small mt-2" role="note">
          <p>{changedFromDefaultText(differences)}</p>
          <button
            type="button"
            className="btn mt-1 inline-flex items-center gap-1"
            onClick={() => reset.mutate()}
            disabled={busy}
          >
            <RotateCcw size={14} aria-hidden /> Reset to default
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
        <label className="field-label" htmlFor="admin-categories">
          Allowed categories
        </label>
        <input
          id="admin-categories"
          value={categories}
          placeholder="Any category"
          aria-describedby="admin-categories-hint"
          onChange={(event) => setCategories(event.target.value)}
          disabled={!live || busy}
          className="rounded-[12px] border border-[var(--line-strong)] bg-[var(--surface)] px-3 py-1.5"
        />
        <p id="admin-categories-hint" className="muted small">
          Catalogue category codes or their prefixes, separated by commas. Leave empty to allow any category.
        </p>
        <fieldset disabled={!live || busy} className="grid grid-cols-2 gap-2">
          <legend className="field-label">Valid (leave empty for no limit)</legend>
          <label className="grid gap-1 small">
            From
            <input
              type="date"
              value={validFrom}
              onChange={(event) => setValidFrom(event.target.value)}
              className="rounded-[12px] border border-[var(--line-strong)] bg-[var(--surface)] px-3 py-1.5"
            />
          </label>
          <label className="grid gap-1 small">
            Until
            <input
              type="date"
              value={validTo}
              onChange={(event) => setValidTo(event.target.value)}
              className="rounded-[12px] border border-[var(--line-strong)] bg-[var(--surface)] px-3 py-1.5"
            />
          </label>
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

export const MAX_PREFERENCES_LENGTH = 1000
const PREFERENCES_EXAMPLE =
  'e.g. Our head chef prefers cream rather than milk for cooking and baking, when appropriate.'

/**
 * #174: free-text restaurant preferences the purchasing agent's language model reads as soft context on later
 * requests. Never policy: limits, allergens, requirements and approvals stay deterministic. Saving creates a new,
 * audited mandate version like any other buyer-admin change.
 */
export function RestaurantPreferences({
  mandate,
  live,
  onChanged,
}: {
  mandate: Mandate
  live: boolean
  onChanged?: (result: MandateChangeResult) => void
}) {
  const queryClient = useQueryClient()
  const saved = mandate.restaurantPreferences ?? ''
  const [draft, setDraft] = useState(saved)
  const [message, setMessage] = useState<string>()
  useEffect(() => {
    setDraft(saved)
  }, [mandate.customerId, saved])

  const save = useMutation({
    mutationFn: (text: string) => {
      const value = text.trim()
      if (value.length > MAX_PREFERENCES_LENGTH)
        throw new Error(`Keep the preferences under ${MAX_PREFERENCES_LENGTH} characters.`)
      if (value === saved) throw new Error('Nothing to change.')
      return changeMandate(mandate.customerId, { restaurantPreferences: value })
    },
    onSuccess: (data) => {
      setMessage(
        data.mandate.restaurantPreferences
          ? `Preferences saved · mandate v${data.mandate.version}`
          : `Preferences cleared · mandate v${data.mandate.version}`,
      )
      queryClient.invalidateQueries({ queryKey: ['mandate-default', mandate.customerId] })
      queryClient.invalidateQueries({ queryKey: ['mandates'] })
      onChanged?.(data)
    },
    onMutate: () => setMessage(undefined),
  })
  const busy = save.isPending
  const tooLong = draft.trim().length > MAX_PREFERENCES_LENGTH

  return (
    <section className="panel" aria-labelledby="restaurant-preferences-heading">
      <h2 id="restaurant-preferences-heading" className="panel-title">
        Restaurant preferences
      </h2>
      <p className="muted small">
        Kitchen habits the agent considers when it reads a request. They never override what a request asks
        for, allergen or dietary requirements, spending limits or approvals.
      </p>
      <form
        className="mt-2 grid gap-2"
        onSubmit={(event) => {
          event.preventDefault()
          save.mutate(draft)
        }}
      >
        <label className="field-label" htmlFor="restaurant-preferences">
          Preferences for {mandate.organisationName}
        </label>
        <textarea
          id="restaurant-preferences"
          rows={4}
          value={draft}
          placeholder={PREFERENCES_EXAMPLE}
          aria-describedby="restaurant-preferences-count"
          aria-invalid={tooLong}
          onChange={(event) => setDraft(event.target.value)}
          disabled={!live || busy}
          className="rounded-[12px] border border-[var(--line-strong)] bg-[var(--surface)] px-3 py-1.5"
        />
        <p id="restaurant-preferences-count" className={`small ${tooLong ? 'text-[var(--block)]' : 'muted'}`}>
          {draft.trim().length} / {MAX_PREFERENCES_LENGTH} characters
        </p>
        <div className="flex flex-wrap gap-2">
          <button type="submit" className="btn btn-primary" disabled={!live || busy || tooLong}>
            Save preferences
          </button>
          <button
            type="button"
            className="btn"
            disabled={!live || busy || !saved}
            onClick={() => {
              setDraft('')
              save.mutate('')
            }}
          >
            Clear preferences
          </button>
        </div>
      </form>
      {save.error && (
        <p className="notice notice-error small mt-2" role="alert">
          {save.error.message}
        </p>
      )}
      {message && (
        <p className="notice small mt-2" role="status">
          {message}
        </p>
      )}
    </section>
  )
}

/**
 * T46 `/admin` view: buyer-admin mandate controls moved off the buyer page and explicitly labelled
 * "Buyer admin".
 */
export function BuyerAdmin() {
  const [adminEntered, setAdminEntered] = useState(() => Boolean(sessionStorage.getItem('verified-or-ask.demo-admin-key')))
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
          <h1 className="text-xl font-bold">Buyer admin</h1>
        </div>
        <Link to="/" className="font-semibold text-[var(--green)] underline">
          Buyer view
        </Link>
      </header>
      <section className="panel">
        <p>Admin changes need the public admin key.</p>
        <button type="button" className="button" disabled={adminEntered} onClick={() => {
          sessionStorage.setItem('verified-or-ask.demo-admin-key', 'demo-admin')
          setAdminEntered(true)
        }}>{adminEntered ? 'Admin key entered' : 'Enter admin key'}</button>
      </section>
      <div className="main-grid">
        <aside className="main-side panel">
          {mandates.error ? (
            <p className="notice notice-error" role="alert">
              Could not load mandates.
            </p>
          ) : (
            <MandatePanel
              mandates={mandates.data ?? []}
              selected={mandate}
              onSelect={setCustomerId}
              loading={mandates.isPending || (Boolean(mandates.data?.length) && !mandate)}
            />
          )}
        </aside>
        <div className="main-flow">
          {mandates.isPending && (
            <LoadingSection label="admin controls" variant="admin" className="panel" />
          )}
          {mandate && adminEntered && (
            <MandateControls
              mandate={mandate}
              live={live}
              onChanged={() => {
                mandates.refetch()
              }}
            />
          )}
          {mandate && adminEntered && (
            <RestaurantPreferences
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
