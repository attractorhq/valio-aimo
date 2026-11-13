import type { Proposal } from '../../lib/contracts'
import {
  formatClock,
  formatDate,
  formatMoney,
  formatNumber,
  formatQuantity,
  formatTime,
} from '../../lib/format'
import { TrustBadge } from '../trust/TrustBadge'
import { TrustCard } from '../trust/TrustCard'

/**
 * Step 3 Order proposal at fixed price + per-line Agent Trust Labels v0.1 cards (P6 P0-2).
 */
export function OrderProposalView({ proposal }: { proposal: Proposal }) {
  const itemsById = new Map(proposal.request.items.map((item) => [item.itemId, item]))
  const excluded = proposal.excluded ?? []

  return (
    <div>
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
            {proposal.lines.map((line) => {
              const priceFact = line.facts.find((f) => f.fact === 'price.unit')
              const stockFact = line.facts.find((f) => f.fact === 'stock.quantity')
              const dateFact = line.facts.find((f) => f.fact === 'delivery.date')
              return (
                <tr key={line.lineId}>
                  <th scope="row">
                    <div>
                      {line.name}
                      <small className="muted"> · {line.productId}</small>
                    </div>
                    {stockFact && (
                      <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs font-normal">
                        <span className="muted">{stockFact.display}</span>
                        <TrustBadge level={stockFact.level} conflict={stockFact.conflict} />
                      </div>
                    )}
                  </th>
                  <td className="num" data-label="Quantity">
                    <span>
                      {formatNumber(line.quantitySalesUnits)} {line.salesUnitLabel}
                      <small className="muted block">
                        = {formatQuantity(line.deliveredQuantity.value, line.deliveredQuantity.unit)}
                      </small>
                    </span>
                  </td>
                  <td className="num" data-label="Unit price">
                    <span>
                      {formatMoney(line.unitPriceExclVat)}
                      {priceFact && (
                        <small className="mt-1 block">
                          <TrustBadge level={priceFact.level} conflict={priceFact.conflict} />
                        </small>
                      )}
                    </span>
                  </td>
                  <td className="num" data-label="Line total">
                    {formatMoney(line.lineTotalExclVat)}
                  </td>
                  <td data-label="Delivery">
                    <span>
                      {formatDate(line.deliveryDate)}
                      {line.closingTime && (
                        <small className="muted block">
                          order by {formatTime(line.closingTime)}
                        </small>
                      )}
                      {dateFact && (
                        <small className="mt-1 block">
                          <TrustBadge level={dateFact.level} conflict={dateFact.conflict} />
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
          <dd>{formatMoney(proposal.totals.exclVat)}</dd>
        </div>
        <div>
          <dt>VAT</dt>
          <dd>{formatMoney(proposal.totals.vat)}</dd>
        </div>
        <div>
          <dt>Total incl. VAT</dt>
          <dd>{formatMoney(proposal.totals.inclVat)}</dd>
        </div>
      </dl>

      <p className="muted small">
        Prices fixed in offer v{proposal.offerVersion} until {formatClock(proposal.validUntil)} ·
        bound to mandate v{proposal.mandateVersion}
      </p>

      <div className="mt-3 grid gap-3" aria-label="Order line trust cards">
        {proposal.lines.map((line) => {
          const item = itemsById.get(line.itemId)
          const excludedSubstitutes = excluded.filter(
            (e) => e.substituteFor === line.productId || (e.itemId === line.itemId && e.reasons.includes('SUBSTITUTE_REJECTED')),
          )
          return (
            <TrustCard
              key={line.lineId}
              line={line}
              item={item}
              excludedSubstitutes={excludedSubstitutes}
            />
          )
        })}
      </div>
    </div>
  )
}
