import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ShieldCheck } from 'lucide-react'
import { money, request, type Identity } from '../lib/api'
import type { Policy } from '../lib/types'
import { ErrorNotice, Loading } from '../components/Common'
import { Button } from '../components/ui/button'

export function Settings({ identity, mockMode }: { identity: Identity; mockMode: boolean }) {
  const cache = useQueryClient()
  const { data, error, isPending } = useQuery({
    queryKey: ['policies', identity],
    queryFn: () => request<Policy[]>(identity, '/api/v1/policies'),
  })
  const [limits, setLimits] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState<string | null>(null)
  const [actionError, setError] = useState<unknown>(null)
  const [saved, setSaved] = useState<string | null>(null)
  async function save(policy: Policy) {
    setBusy(policy.buyer_id)
    setError(null)
    setSaved(null)
    try {
      await request(identity, `/api/v1/policies/${policy.buyer_id}`, {
        method: 'PATCH',
        body: JSON.stringify({ max_auto_total: limits[policy.buyer_id] ?? policy.max_auto_total }),
      })
      setSaved(policy.buyer_id)
      void cache.invalidateQueries({ queryKey: ['policies'] })
    } catch (e) {
      setError(e)
    } finally {
      setBusy(null)
    }
  }
  return (
    <>
      <div className="page-heading">
        <span className="eyebrow">THE RULES OF ENGAGEMENT</span>
        <h1>Define the boundaries.</h1>
        <p>Illustrative buyer policies. Enforced by the server, independently of the assistant.</p>
      </div>
      <ErrorNotice error={error || actionError} />
      {isPending && <Loading />}
      <div className="settings-grid">
        {data?.map((p) => (
          <section className="settings-card" key={p.buyer_id}>
            <div className="section-heading">
              <div className="inline-icon">
                <ShieldCheck size={24} />
                <h2>{p.name}</h2>
              </div>
              <span className="badge">VERSION {p.version}</span>
            </div>
            <div className="settings-limit">
              <span>Automatic approval threshold</span>
              <strong>{money(p.max_auto_total)}</strong>
              <p>Above this amount, a manager must explicitly approve.</p>
            </div>
            <dl>
              <dt>Allowed categories</dt>
              <dd>
                {p.allowed_categories.slice(0, 12).join(', ')}
                {p.allowed_categories.length > 12
                  ? ` … (${p.allowed_categories.length} supplier category codes; seeded challenge policy permits all imported categories)`
                  : ''}
              </dd>
              <dt>Substitutions</dt>
              <dd>
                {p.allow_equivalent_substitutions
                  ? 'Only predefined equivalent products are automatic'
                  : 'Always require human approval'}
              </dd>
              <dt>Availability</dt>
              <dd>Confirmed quantity and delivery date required</dd>
              <dt>Allergen restrictions</dt>
              <dd>
                {p.excluded_allergens.join(', ') ||
                  'No buyer-wide exclusions; request-specific restrictions always apply'}
              </dd>
              <dt>Unusual quantity</dt>
              <dd>Over {p.unusual_quantity} base units per line → human review</dd>
              <dt>Hard safety rule</dt>
              <dd>
                Explicit allergen conflicts are rejected, even by a manager. Missing facts are
                unknown, never declared safe.
              </dd>
            </dl>
            {identity === 'demo-manager' && mockMode && (
              <form
                onSubmit={(e) => {
                  e.preventDefault()
                  void save(p)
                }}
                className="settings-form"
              >
                <label>
                  Demo threshold · EUR
                  <input
                    type="number"
                    required
                    min="0"
                    step="0.01"
                    value={limits[p.buyer_id] ?? p.max_auto_total}
                    onChange={(e) => setLimits((v) => ({ ...v, [p.buyer_id]: e.target.value }))}
                  />
                </label>
                <Button disabled={busy === p.buyer_id}>
                  {busy === p.buyer_id ? 'Saving…' : 'Save new policy version'}
                </Button>
                {saved === p.buyer_id && (
                  <p className="success-text" role="status">
                    Policy saved. Prior authorizations must be revalidated.
                  </p>
                )}
              </form>
            )}
          </section>
        ))}
      </div>
      <div className="info-notice">
        <ShieldCheck size={23} />
        <p>
          <strong>Demo authentication, not production security.</strong> The public identities are
          for judging only. A production deployment needs verified buyer credentials,
          organization-scoped manager roles, rate limits, and durable audit retention. No upstream
          API credentials are sent to the browser.
        </p>
      </div>
    </>
  )
}
