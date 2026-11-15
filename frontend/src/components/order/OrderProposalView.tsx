import type { Proposal } from '../../lib/contracts'
import {
  formatClock,
  formatDate,
  formatMoney,
  formatNumber,
  formatQuantity,
  formatTime,
} from '../../lib/format'
import { ProposalRefText } from '../trust/ProposalRefText'
import { TrustBadge } from '../trust/TrustBadge'
import { TrustCard } from '../trust/TrustCard'
import { SplitProposalSummary } from '../proposal/SplitProposalSummary'
import { Alternatives } from './Alternatives'

/**
 * Step 3 Order proposal at fixed price + per-line Agent Trust Labels v0.1 cards (P6 P0-2) + Good/Better/Best alternatives (T22).
 */
export function OrderProposalView({
  proposal,
  offer,
  request: directRequest,
  mandateVersion,
  forbiddenAllergens,
  plan,
  showEvidence,
}: {
  proposal?: Proposal
  offer?: Proposal | import('../../lib/contracts').Offer
  request?: Proposal['request']
  mandateVersion?: number
  forbiddenAllergens?: readonly string[]
  plan?: unknown[] | null
  showEvidence?: boolean
}) {
  const source = proposal ?? offer
  if (!source) return null
  const req = directRequest ?? proposal?.request
  const itemsById = new Map((req?.items ?? []).map((item) => [item.itemId, item]))
  const excluded = source.excluded ?? []
  const resolvedMandateVer = proposal?.mandateVersion ?? mandateVersion ?? 1

  return (
    <div>
      <SplitProposalSummary proposal={proposal} offer={offer} request={req} />
      <div className="table-scroll">
        <table className="data-table order-table stack-on-mobile">
          <caption className="visually-hidden">Order proposal lines</caption>
          <thead>
            <tr>
              <th scope="col">Product</th>
              <th scope="col" className="num">
                Quantity
              </th>
              <th scope="col" className="num">
                Unit price
              </th>
              <th scope="col" className="num">
                Line total
              </th>
              <th scope="col">Delivery</th>
            </tr>
          </thead>
          <tbody>
            {source.lines.map((line) => {
              const stockFact = line.facts.find((f) => f.fact === 'stock.quantity')
              return (
                <tr key={line.lineId}>
                  <th scope="row">
                    <div>
                      {line.name}
                      <small className="muted"> · {line.productId}</small>
                    </div>
                    {stockFact && (
                      <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-xs font-normal">
                        <span className="muted" title={stockFact.method ?? undefined}>
                          <ProposalRefText text={stockFact.display} />
                        </span>
                        <TrustBadge level={stockFact.level} conflict={stockFact.conflict} />
                      </div>
                    )}
                  </th>
                  <td className="num" data-label="Quantity">
                    <span>
                      {formatNumber(line.quantitySalesUnits)} {line.salesUnitLabel}
                      <small className="muted block">
                        ={' '}
                        {formatQuantity(line.deliveredQuantity.value, line.deliveredQuantity.unit)}
                      </small>
                    </span>
                  </td>
                  <td className="num" data-label="Unit price">
                    <span>{formatMoney(line.unitPriceExclVat)}</span>
                  </td>
                  <td className="num" data-label="Line total">
                    {formatMoney(line.lineTotalExclVat)}
                  </td>
                  <td data-label="Delivery">
                    <span>
                      {formatDate(line.deliveryDate)}
                      {line.deliveryDate > source.requestedDate && (
                        <span className="block text-xs">
                          After your {formatDate(source.requestedDate)} deadline
                        </span>
                      )}
                      {line.closingTime && (
                        <small className="muted block">
                          order by {formatTime(line.closingTime)}
                        </small>
                      )}
                    </span>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      <dl className="totals">
        <div>
          <dt>Total excl. VAT</dt>
          <dd>{formatMoney(source.totals.exclVat)}</dd>
        </div>
        <div>
          <dt>VAT</dt>
          <dd>{formatMoney(source.totals.vat)}</dd>
        </div>
        <div>
          <dt>Total incl. VAT</dt>
          <dd>{formatMoney(source.totals.inclVat)}</dd>
        </div>
      </dl>

      <p className="muted small my-0.5">
        Prices fixed until {formatClock(source.validUntil)} · mandate v{resolvedMandateVer}
      </p>

      <div className="mt-0.5 grid gap-0.5" aria-label="Order line trust cards">
        {source.lines.map((line) => {
          const item = itemsById.get(line.itemId)
          const excludedSubstitutes = excluded.filter(
            (e) =>
              e.substituteFor === line.productId ||
              (e.itemId === line.itemId && e.reasons.includes('SUBSTITUTE_REJECTED')),
          )
          return (
            <TrustCard
              key={line.lineId}
              line={line}
              item={item}
              forbiddenAllergens={forbiddenAllergens}
              excludedSubstitutes={excludedSubstitutes}
              showEvidence={showEvidence}
            />
          )
        })}
      </div>

      <Alternatives plan={plan} proposal={proposal} offer={offer} request={req} />
    </div>
  )
}
