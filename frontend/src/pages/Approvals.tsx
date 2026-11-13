import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, Clock3, ShieldCheck, X } from 'lucide-react'
import { Link } from 'react-router-dom'
import { money, readableDate, readableTime, request, type Identity } from '../lib/api'
import type { Proposal } from '../lib/types'
import { EmptyState, ErrorNotice, Loading, StatusBadge } from '../components/Common'
import { ProposalCard } from '../components/ProposalCard'
import { Button } from '../components/ui/button'

export function Approvals({ identity }: { identity: Identity }) {
  const cache = useQueryClient()
  const { data, isPending, error } = useQuery({
    queryKey: ['proposals', identity],
    queryFn: () => request<Proposal[]>(identity, '/api/v1/proposals'),
    refetchInterval: 10_000,
  })
  const pending = data?.filter((p) => p.status === 'NEEDS_APPROVAL') || []
  const authorized = data?.filter((p) => ['AUTO_APPROVED', 'APPROVED'].includes(p.status)) || []
  const manager = identity === 'demo-manager'
  const [actionError, setActionError] = useState<unknown>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [notes, setNotes] = useState<Record<string, string>>({})
  async function review(p: Proposal, action: 'approve' | 'reject') {
    setBusy(p.id)
    setActionError(null)
    try {
      await request(identity, `/api/v1/proposals/${p.id}/${action}`, {
        method: 'POST',
        body: JSON.stringify({ note: notes[p.id] || '' }),
      })
    } catch (e) {
      setActionError(e)
    } finally {
      setBusy(null)
      void cache.invalidateQueries({ queryKey: ['proposals'] })
      void cache.invalidateQueries({ queryKey: ['audit'] })
    }
  }
  return (
    <>
      <div className="page-heading">
        <span className="eyebrow">HUMAN IN THE LOOP</span>
        <h1>Confidence at every checkpoint.</h1>
        <p>Review exceptions, not every purchase. Your authorization is tied to checked terms.</p>
      </div>
      <div className="stats-row">
        <div>
          <Clock3 size={21} />
          <strong>{pending.length}</strong>
          <span>Awaiting review</span>
        </div>
        <div>
          <ShieldCheck size={21} />
          <strong>{authorized.length}</strong>
          <span>Ready to commit</span>
        </div>
        <div>
          <Check size={21} />
          <strong>{data?.filter((p) => p.status === 'COMMITTED').length || 0}</strong>
          <span>Simulated orders</span>
        </div>
      </div>
      {!manager && (
        <div className="info-notice">
          <ShieldCheck size={20} />
          <p>
            You’re a buyer. You can track your proposals here, but only the{' '}
            <strong>Demo manager</strong> identity can approve or reject. Switch identities in the
            sidebar to demonstrate human review.
          </p>
        </div>
      )}
      <ErrorNotice error={error || actionError} />
      {isPending && <Loading />}
      <div className="section-heading">
        <h2>Approval queue</h2>
        <span className="mini-label">REFRESHES EVERY 10 SECONDS</span>
      </div>
      {!isPending && !pending.length && (
        <EmptyState title="Nothing waiting for a sign-off">
          Requests outside buyer authority appear here. Try the “Over limit” scenario in the buyer
          assistant.
        </EmptyState>
      )}
      <div className="approval-list">
        {pending.map((p) => (
          <section className="approval-card" key={p.id}>
            <div className="section-heading">
              <div>
                <span className="eyebrow">
                  {p.buyer_name} · POLICY V{p.policy_version}
                </span>
                <h3>
                  {money(p.offer.total)} <small>EUR</small>
                </h3>
              </div>
              <StatusBadge status={p.status} />
            </div>
            <p>{p.request.request}</p>
            <div className="approval-lines">
              {p.offer.items.map((i) => (
                <div key={i.product_code}>
                  <span>
                    {i.product_name}
                    <small>
                      {i.sales_quantity && i.sales_unit && i.sales_unit_price
                        ? `${i.sales_quantity} ${i.sales_unit} × ${money(i.sales_unit_price)} / ${i.sales_unit} (${i.quantity} ${i.unit} requested)`
                        : `${i.quantity} ${i.unit} × ${money(i.unit_price)} / ${i.unit}`}
                    </small>
                  </span>
                  <strong>{money(i.line_total)}</strong>
                </div>
              ))}
            </div>
            <div className="meta-row">
              <span>Delivery {readableDate(p.offer.delivery_date)}</span>
              <span>Requested {readableTime(p.created_at)}</span>
            </div>
            <ul className="approval-reasons">
              {p.reasons.map((r, index) => (
                <li key={index}>
                  <code>{r.code}</code>
                  <span>{r.message}</span>
                </li>
              ))}
            </ul>
            {manager && (
              <form
                onSubmit={(e) => {
                  e.preventDefault()
                  void review(p, 'approve')
                }}
              >
                <label>
                  Review note{' '}
                  <textarea
                    minLength={3}
                    maxLength={1000}
                    required
                    placeholder="Why are you authorizing or rejecting this exception?"
                    value={notes[p.id] || ''}
                    onChange={(e) => setNotes((n) => ({ ...n, [p.id]: e.target.value }))}
                    rows={2}
                  />
                </label>
                <div className="review-actions">
                  <Button
                    variant="outline"
                    type="button"
                    disabled={busy === p.id || (notes[p.id]?.trim().length || 0) < 3}
                    onClick={() => review(p, 'reject')}
                  >
                    <X size={15} />
                    Reject
                  </Button>
                  <Button disabled={busy === p.id}>
                    {busy === p.id ? 'Revalidating…' : 'Approve checked terms'}
                    <Check size={16} />
                  </Button>
                </div>
              </form>
            )}
            <Link className="small" to={`/history?proposal=${p.id}`}>
              Inspect full decision trail ↗
            </Link>
          </section>
        ))}
      </div>
      {authorized.length > 0 && (
        <>
          <div className="section-heading mt-section">
            <h2>Authorized · awaiting explicit commit</h2>
          </div>
          <div className="authorized-grid">
            {authorized.map((p) => (
              <ProposalCard
                key={p.id}
                proposal={p}
                identity={identity}
                onUpdate={() => {
                  void cache.invalidateQueries({ queryKey: ['proposals'] })
                }}
              />
            ))}
          </div>
        </>
      )}
    </>
  )
}
