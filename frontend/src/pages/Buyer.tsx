import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ArrowRight,
  ArrowUpRight,
  Check,
  CircleCheck,
  FlaskConical,
  Leaf,
  Milk,
  Package,
  Search,
  ShieldCheck,
  SlidersHorizontal,
  Truck,
} from 'lucide-react'
import { Link } from 'react-router-dom'
import { money, readableDate, readableTime, request, type Identity } from '../lib/api'
import type {
  Context,
  Offer,
  OfferLine,
  Policy,
  Product,
  Proposal,
  ProposalRequest,
} from '../lib/types'
import { Button } from '../components/ui/button'
import { EmptyState, ErrorNotice, Loading } from '../components/Common'
import { ProposalCard } from '../components/ProposalCard'
import { Chat } from '../components/Chat'

type Candidate = { product: Product; offer?: OfferLine; error?: string }
type Fields = {
  query: string
  quantity: string
  unit: string
  date: string
  budget: string
  dietary: string
  allergens: string
}

function verifiedAllergens(product: Product, text: string) {
  const restrictions = text
    .split(',')
    .map((a) => a.trim().toLowerCase())
    .filter(Boolean)
  return (
    !restrictions.length ||
    (product.allergens !== null &&
      restrictions.every(
        (a) =>
          !product.allergens!.includes(a) &&
          (product.free_from_allergens === null || product.free_from_allergens.includes(a)),
      ))
  )
}

export function Buyer({ identity, context }: { identity: Identity; context: Context }) {
  const cache = useQueryClient()
  const { data: policies } = useQuery({
    queryKey: ['policies', identity],
    queryFn: () => request<Policy[]>(identity, '/api/v1/policies'),
  })
  const [fields, setFields] = useState<Fields>({
    query: 'milk',
    quantity: '100',
    unit: 'L',
    date: context.next_monday,
    budget: '200',
    dietary: 'lactose_free',
    allergens: '',
  })
  const [candidates, setCandidates] = useState<Candidate[] | null>(null)
  const [verifiedFields, setVerifiedFields] = useState<Fields | null>(null)
  const [busy, setBusy] = useState(false)
  const [proposing, setProposing] = useState<string | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [proposal, setProposal] = useState<Proposal | null>(null)
  const [substitution, setSubstitution] = useState(false)
  const policy = policies?.[0]
  function update(key: keyof Fields, value: string) {
    setFields((f) => ({ ...f, [key]: value }))
    setCandidates(null)
    setVerifiedFields(null)
  }
  function preset(which: 'normal' | 'approval' | 'unknown') {
    setFields((f) => ({
      ...f,
      query: which === 'unknown' ? 'soup' : 'milk',
      quantity: which === 'approval' ? '200' : which === 'unknown' ? '10' : '100',
      unit: 'L',
      budget: '200',
      dietary: which === 'unknown' ? '' : 'lactose_free',
      allergens: which === 'unknown' ? 'milk' : '',
    }))
    setCandidates(null)
    setVerifiedFields(null)
    setError(null)
    setSubstitution(false)
  }
  async function find(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    setCandidates(null)
    setProposal(null)
    const snapshot = { ...fields }
    try {
      const params = new URLSearchParams({
        query: snapshot.query,
        unit: snapshot.unit,
        limit: context.mock_data ? '20' : '6',
      })
      if (snapshot.dietary) params.set('dietary', snapshot.dietary)
      const products = await request<Product[]>(identity, `/api/v1/products?${params}`)
      const results: Candidate[] = await Promise.all(
        products
          .filter((p) => p.unit === snapshot.unit)
          .map(async (product) => {
            try {
              const offer = await request<Offer>(identity, '/api/v1/check-offer', {
                method: 'POST',
                body: JSON.stringify({
                  items: [
                    {
                      product_code: product.code,
                      quantity: snapshot.quantity,
                      unit: snapshot.unit,
                    },
                  ],
                  delivery_date: snapshot.date,
                }),
              })
              return { product, offer: offer.items[0] }
            } catch (e) {
              return { product, error: e instanceof Error ? e.message : 'Verification failed' }
            }
          }),
      )
      function feasible(c: Candidate) {
        return !!c.offer?.available && verifiedAllergens(c.product, snapshot.allergens)
      }
      results.sort(
        (a, b) =>
          Number(feasible(b)) - Number(feasible(a)) ||
          Number(!!b.offer?.available) - Number(!!a.offer?.available) ||
          Number(a.offer?.line_total ?? Infinity) - Number(b.offer?.line_total ?? Infinity) ||
          (b.product.reliability ?? 0) - (a.product.reliability ?? 0) ||
          a.product.code.localeCompare(b.product.code),
      )
      setCandidates(results)
      setVerifiedFields(snapshot)
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
    }
  }
  async function propose(candidate: Candidate) {
    if (!verifiedFields) return
    setProposing(candidate.product.code)
    setError(null)
    const f = verifiedFields
    const payload: ProposalRequest = {
      items: [
        {
          product_code: candidate.product.code,
          quantity: f.quantity,
          unit: f.unit,
          ...(substitution ? { substitute_for: 'DEMO-MLK-01' } : {}),
        },
      ],
      delivery_date: f.date,
      budget: f.budget || null,
      required_dietary: f.dietary ? [f.dietary] : [],
      excluded_allergens: f.allergens
        .split(',')
        .map((x) => x.trim().toLowerCase())
        .filter(Boolean),
      request: `${f.quantity} ${f.unit} ${f.query} by ${f.date}; ${f.dietary || 'no dietary filter'}; budget ${f.budget || 'not specified'} EUR; excluded allergens: ${f.allergens || 'none'}.`,
    }
    try {
      setProposal(
        await request<Proposal>(identity, '/api/v1/proposals', {
          method: 'POST',
          body: JSON.stringify(payload),
        }),
      )
      void cache.invalidateQueries({ queryKey: ['proposals'] })
      void cache.invalidateQueries({ queryKey: ['audit'] })
    } catch (e) {
      setError(e)
    } finally {
      setProposing(null)
    }
  }
  if (identity === 'demo-manager')
    return (
      <>
        <div className="page-heading">
          <span className="eyebrow">HUMAN IN THE LOOP</span>
          <h1>You have the final say.</h1>
          <p>
            You’re viewing the demo as a manager. Review pending purchases or switch to a buyer to
            start a request.
          </p>
        </div>
        <div className="manager-welcome">
          <ShieldCheck size={48} />
          <h2>Good autonomy has boundaries.</h2>
          <p>
            Buyers can propose. The server checks policy. Only a manager can authorize exceptions.
          </p>
          <Button asChild>
            <Link to="/approvals">
              Open approval queue
              <ArrowRight size={16} />
            </Link>
          </Button>
        </div>
      </>
    )
  return (
    <>
      <div className="page-heading">
        <div className="eyebrow">
          <span className="live-dot" /> YOUR KITCHEN. YOUR RULES.
        </div>
        <h1>A smarter way to stock up.</h1>
        <p>
          From kitchen needs to a verified purchase. Let agents do the legwork.
          <br className="desktop-break" /> Keep every decision within your control.
        </p>
      </div>
      <div className="buyer-grid">
        <div className="buyer-main">
          <Chat identity={identity} aiConfigured={context.ai_configured} onProposal={setProposal} />
          <section className="request-panel">
            <div className="section-heading">
              <div className="inline-icon">
                <SlidersHorizontal size={18} />
                <h2>Build a purchase request</h2>
              </div>
              <span className="step-label">01 / DISCOVER</span>
            </div>
            {!context.mock_data && (
              <p className="small muted">
                Live calls to Valio’s synthetic challenge backend. Only six shortlisted products are
                checked; no production data or real orders.
              </p>
            )}
            <div className="scenario-bar">
              <span>Try a scenario</span>
              <button onClick={() => preset('normal')}>Within policy</button>
              <button onClick={() => preset('approval')}>Over limit</button>
              <button onClick={() => preset('unknown')}>Unknown allergens</button>
            </div>
            <form onSubmit={find}>
              <label className="search-label">
                What does your kitchen need?
                <div className="input-icon">
                  <Search size={18} />
                  <input
                    required
                    placeholder="Product name or code"
                    maxLength={200}
                    value={fields.query}
                    onChange={(e) => update('query', e.target.value)}
                    disabled={busy}
                  />
                </div>
              </label>
              <div className="form-grid">
                <label>
                  Quantity
                  <input
                    type="number"
                    required
                    min="0.001"
                    step="0.001"
                    value={fields.quantity}
                    onChange={(e) => update('quantity', e.target.value)}
                    disabled={busy}
                  />
                </label>
                <label>
                  Unit
                  <select
                    value={fields.unit}
                    onChange={(e) => update('unit', e.target.value)}
                    disabled={busy}
                  >
                    <option value="L">Litres (L)</option>
                    <option value="KG">Kilograms (KG)</option>
                    <option value="EA">Items (EA)</option>
                    {context.units
                      ?.filter((u) => !['L', 'KG', 'EA'].includes(u))
                      .map((u) => (
                        <option key={u} value={u}>
                          Supplier unit ({u})
                        </option>
                      ))}
                  </select>
                </label>
                <label>
                  Delivery date
                  <input
                    type="date"
                    min={context.today}
                    value={fields.date}
                    onChange={(e) => update('date', e.target.value)}
                    required
                    disabled={busy}
                  />
                </label>
                <label>
                  Budget · EUR
                  <input
                    type="number"
                    min="0"
                    step="0.01"
                    value={fields.budget}
                    onChange={(e) => update('budget', e.target.value)}
                    placeholder="Optional"
                    disabled={busy}
                  />
                </label>
              </div>
              <div className="form-grid two">
                <label>
                  Dietary requirement
                  <select
                    value={fields.dietary}
                    onChange={(e) => update('dietary', e.target.value)}
                    disabled={busy}
                  >
                    <option value="">No requirement</option>
                    <option value="lactose_free">Lactose-free</option>
                    <option value="vegan">Vegan</option>
                    <option value="gluten_free">Gluten-free</option>
                  </select>
                </label>
                <label>
                  Exclude allergens
                  <input
                    value={fields.allergens}
                    onChange={(e) => update('allergens', e.target.value)}
                    placeholder="e.g. milk, gluten"
                    disabled={busy}
                  />
                </label>
              </div>
              <div className="form-footer">
                <span>
                  <Truck size={15} /> Next Monday: {readableDate(context.next_monday)} · Helsinki
                </span>
                <Button disabled={busy}>
                  {busy ? 'Verifying…' : 'Find & verify options'}
                  <ArrowRight size={16} />
                </Button>
              </div>
            </form>
          </section>
          <ErrorNotice error={error} />
          {busy && (
            <Loading label="Searching catalog, then checking fixed prices and date-specific stock…" />
          )}
          {candidates && (
            <section className="results-section">
              <div className="section-heading">
                <div>
                  <h2>
                    Verified options <span className="count">{candidates.length}</span>
                  </h2>
                  <p className="small muted">
                    Constraints first, then availability, price and reliability. No silent
                    substitutions.
                  </p>
                </div>
                <span className="step-label">02 / COMPARE</span>
              </div>
              {candidates.length === 0 ? (
                <EmptyState title="No matching products">
                  Try another search, unit or dietary filter. Unknown dietary facts are not returned
                  as confirmed matches.
                </EmptyState>
              ) : (
                <div className="product-grid">
                  {candidates.map((c, index) => (
                    <ProductCard
                      key={c.product.code}
                      candidate={c}
                      recommended={
                        index === 0 &&
                        !!c.offer?.available &&
                        verifiedAllergens(c.product, verifiedFields?.allergens || '')
                      }
                      onSelect={() => propose(c)}
                      disabled={!!proposing}
                      loading={proposing === c.product.code}
                      budget={verifiedFields?.budget || ''}
                      deliveryDate={verifiedFields!.date}
                    />
                  ))}
                </div>
              )}
              {candidates.some((c) => c.product.code === 'DEMO-MLK-02') && (
                <label className="checkbox-label">
                  <input
                    type="checkbox"
                    checked={substitution}
                    onChange={(e) => setSubstitution(e.target.checked)}
                  />{' '}
                  Explicitly mark selection as a substitute for DEMO-MLK-01 (tests buyer
                  substitution rules)
                </label>
              )}
              <p className="disclaimer">
                <FlaskConical size={14} />{' '}
                {context.mock_data
                  ? 'Synthetic fixture prices and availability.'
                  : 'Live Valio challenge API calls against synthetic supplier data.'}{' '}
                Checks do not reserve stock. All facts are revalidated before commitment.
              </p>
            </section>
          )}
          {proposal && (
            <ProposalCard proposal={proposal} identity={identity} onUpdate={setProposal} />
          )}
        </div>
        <aside className="buyer-aside">
          <section className="policy-card">
            <div className="policy-icon">
              <ShieldCheck size={25} />
            </div>
            <span className="eyebrow">AUTONOMY, WITH GUARDRAILS</span>
            <h3>Your buying policy</h3>
            <p>{policy?.name || 'Loading buyer…'}</p>
            <div className="policy-limit">
              <span>Automatic approval up to</span>
              <strong>{policy ? money(policy.max_auto_total) : '—'}</strong>
              <span>per order · EUR</span>
            </div>
            <ul>
              <li>
                <CircleCheck size={16} /> Confirmed availability required
              </li>
              <li>
                <CircleCheck size={16} />{' '}
                {policy?.allow_equivalent_substitutions
                  ? 'Predefined equivalents permitted'
                  : 'No unapproved substitutions'}
              </li>
              <li>
                <CircleCheck size={16} /> Allergen conflicts always blocked
              </li>
              <li>
                <CircleCheck size={16} /> Exceptions go to a manager
              </li>
            </ul>
            <Link to="/settings">
              View policy details
              <ArrowUpRight size={15} />
            </Link>
          </section>
          <section className="how-card">
            <span className="eyebrow">HOW TRUSTED TRADE WORKS</span>
            {[
              ['01', 'Discover & verify', 'Real structure. Explicit units. Point-in-time checks.'],
              ['02', 'Check the boundaries', 'Deterministic server-side rules, not AI judgment.'],
              ['03', 'Approve & commit', 'Human review when needed. Revalidation every time.'],
            ].map(([n, title, text]) => (
              <div className="how-step" key={n}>
                <span>{n}</span>
                <div>
                  <h4>{title}</h4>
                  <p>{text}</p>
                </div>
              </div>
            ))}
          </section>
          <div className="open-standard">
            <div className="inline-icon">
              <span className="code-symbol">{'{ }'}</span>
              <strong>Open by design.</strong>
            </div>
            <p>
              The assistant is just one client. Any buyer agent can use the same documented REST
              API.
            </p>
            <a
              href={`${import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'}/docs`}
              target="_blank"
              rel="noreferrer"
            >
              Explore the API
              <ArrowUpRight size={14} />
            </a>
          </div>
        </aside>
      </div>
    </>
  )
}

function ProductCard({
  candidate: { product: p, offer, error },
  recommended,
  onSelect,
  disabled,
  loading,
  budget,
  deliveryDate,
}: {
  candidate: Candidate
  recommended: boolean
  onSelect: () => void
  disabled: boolean
  loading: boolean
  budget: string
  deliveryDate: string
}) {
  const Icon =
    p.category === 'dairy'
      ? Milk
      : p.category === 'produce' || p.category === 'plant_based'
        ? Leaf
        : Package
  return (
    <article className={`product-card ${recommended ? 'recommended' : ''}`}>
      <div className="product-top">
        <div className={`product-art art-${p.category}`}>
          <Icon size={35} strokeWidth={1.3} />
        </div>
        <span className="mini-label">{p.category.replace('_', ' ')}</span>
        {recommended && (
          <span className="best-fit">
            <Check size={12} /> Best checked price
          </span>
        )}
      </div>
      <span className="product-code">
        {p.code} · order step {p.pack_quantity} {p.unit}
      </span>
      <h3>{p.name}</h3>
      <div className="tags">
        {Object.entries(p.dietary)
          .filter(([, known]) => known === true)
          .map(([key]) => (
            <span key={key}>{key.replaceAll('_', '-')}</span>
          ))}
      </div>
      <p className="allergen-note">
        Allergens:{' '}
        {p.allergens === null
          ? 'unknown — review required if restricted'
          : p.allergens.length
            ? p.allergens.join(', ')
            : p.free_from_allergens === null
              ? 'none declared in fixture'
              : 'no positives declared — absence is not guaranteed'}
      </p>
      {offer ? (
        <>
          <div className="product-price">
            <strong>
              {Number(p.quantity_per_sales_unit) !== 1 ? '≈ ' : ''}
              {money(offer.unit_price)}
              <small> / {offer.unit}</small>
            </strong>
            <span>
              {money(offer.line_total)} for {offer.quantity} {offer.unit}
            </span>
          </div>
          <div className={`availability ${offer.available ? '' : 'unavailable'}`}>
            <i />
            {offer.available
              ? `Available for ${readableDate(deliveryDate)}`
              : offer.availability_status === 'UNKNOWN'
                ? 'Supplier stock unconfirmed — not verified inventory'
                : 'Not available for requested quantity / date'}
          </div>
          {offer.availability_note && <p className="allergen-note">{offer.availability_note}</p>}
          {budget && Number(offer.line_total) > Number(budget) && (
            <span className="over-budget">Over request budget · human review</span>
          )}
        </>
      ) : (
        <ErrorNotice error={error} />
      )}
      <div className="product-metrics">
        <span>
          <Truck size={13} />
          {p.reliability === null
            ? 'Reliability unknown'
            : `${Math.round(p.reliability * 100)}% reliability`}
        </span>
        <span>
          <Leaf size={13} />
          {p.carbon_kg_co2_per_kg === null
            ? 'CO₂ unknown'
            : `${p.carbon_kg_co2_per_kg} kg CO₂e/kg*`}
        </span>
      </div>
      <Button
        variant={recommended ? 'default' : 'outline'}
        className="full-width"
        onClick={onSelect}
        disabled={disabled || !offer}
      >
        {loading
          ? 'Checking policy…'
          : offer?.available
            ? 'Create proposal'
            : 'Check policy / view failure'}
        <ArrowRight size={15} />
      </Button>
      {offer && (
        <details className="source-details">
          <summary>Source & verification</summary>
          <p>
            {offer.source}
            <br />
            Checked {readableTime(offer.verified_at)} Helsinki.
            <br />
            {offer.sales_unit && (
              <>
                {offer.sales_quantity} {offer.sales_unit} × {money(offer.sales_unit_price!)} per
                supplier sales unit. Totals use these unrounded terms.
                <br />
              </>
            )}
            {p.sales_unit && (
              <>
                1 {p.sales_unit} = {p.quantity_per_sales_unit} {p.unit}. Order step is not a whole
                pack.
                <br />
              </>
            )}
            {offer.order_cutoff && (
              <>
                Order cut-off: {readableTime(offer.order_cutoff)} Helsinki.
                <br />
              </>
            )}
            *Carbon is a category average, not this product’s measurement.
          </p>
        </details>
      )}
    </article>
  )
}
