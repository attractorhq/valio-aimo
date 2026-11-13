import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { ArrowRight, Check, CheckCheck, ShieldCheck } from 'lucide-react'
import { Link } from 'react-router-dom'
import { money, readableDate, request, type Identity } from '../lib/api'
import type { Proposal } from '../lib/types'
import { Button } from './ui/button'
import { ErrorNotice, StatusBadge } from './Common'

export function ProposalCard({
  proposal,
  identity,
  onUpdate,
}: {
  proposal: Proposal
  identity: Identity
  onUpdate?: (p: Proposal) => void
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const cache = useQueryClient()
  const canCommit = ['AUTO_APPROVED', 'APPROVED'].includes(proposal.status)
  async function commit() {
    setBusy(true)
    setError(null)
    // Stable per-proposal key also survives reloads and failed network responses.
    try {
      const p = await request<Proposal>(identity, `/api/v1/proposals/${proposal.id}/commit`, {
        method: 'POST',
        headers: { 'Idempotency-Key': `web-commit-${proposal.id}` },
      })
      onUpdate?.(p)
    } catch (e) {
      setError(e)
      try {
        onUpdate?.(await request<Proposal>(identity, `/api/v1/proposals/${proposal.id}`))
      } catch {
        /* Keep original error. */
      }
    } finally {
      setBusy(false)
      void cache.invalidateQueries({ queryKey: ['proposals'] })
      void cache.invalidateQueries({ queryKey: ['audit'] })
    }
  }
  return (
    <section className="proposal-card">
      <div className="section-heading">
        <div className="inline-icon">
          <ShieldCheck size={20} />
          <h3>Purchase proposal</h3>
        </div>
        <StatusBadge status={proposal.status} />
      </div>
      <div className="proposal-id">
        {proposal.buyer_name} · #{proposal.id.slice(0, 8)} · Policy v{proposal.policy_version}
      </div>
      {proposal.offer.items.map((line) => (
        <div className="proposal-line" key={line.product_code}>
          <div>
            <strong>{line.product_name}</strong>
            <span>
              {line.sales_quantity && line.sales_unit && line.sales_unit_price
                ? `${line.sales_quantity} ${line.sales_unit} × ${money(line.sales_unit_price)} / ${line.sales_unit} (${line.quantity} ${line.unit} requested)`
                : `${line.quantity} ${line.unit} × ${money(line.unit_price)} / ${line.unit}`}
            </span>
          </div>
          <strong>{money(line.line_total)}</strong>
        </div>
      ))}
      <div className="proposal-total">
        <span>Delivery {readableDate(proposal.offer.delivery_date)}</span>
        <strong>
          {money(proposal.offer.total)} <small>EUR</small>
        </strong>
      </div>
      <ul className="reason-list">
        {proposal.reasons.map((r, i) => (
          <li key={`${r.code}-${i}`}>
            <Check size={14} />
            <div>
              {r.message}
              <code>{r.code}</code>
            </div>
          </li>
        ))}
      </ul>
      <ErrorNotice error={error} />
      {canCommit && (
        <Button className="full-width" onClick={commit} disabled={busy}>
          {busy ? 'Revalidating…' : 'Commit simulated order'}
          <ArrowRight size={16} />
        </Button>
      )}
      {proposal.status === 'NEEDS_APPROVAL' && (
        <p className="muted small">
          Sent to the <Link to="/approvals">manager queue</Link>. A buyer cannot approve this
          proposal.
        </p>
      )}
      {proposal.order && (
        <div className="order-success">
          <CheckCheck size={21} />
          <div>
            <strong>SIMULATED ORDER · {proposal.order.id.slice(0, 8)}</strong>
            <p>Saved locally. No order was sent to Valio.</p>
          </div>
        </div>
      )}
      <div className="proposal-footer">
        <span>Net prices · VAT excluded · no simulated delivery fee</span>
        <Link to={`/history?proposal=${proposal.id}`}>View audit trail ↗</Link>
      </div>
    </section>
  )
}
