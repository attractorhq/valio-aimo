import { Quote } from 'lucide-react'
import type { Proposal } from '../../lib/contracts'
import { formatDate, formatQuantity, requirementLabel } from '../../lib/format'
import { AssumptionChips, formatPreferenceValue } from './AssumptionChips'

/** Step 1: the structured request, with editable assumption chips (§7.2, T24). */
export function RequestCard({
  proposal,
  request: directRequest,
  onEditAssumption,
  disabled = false,
}: {
  proposal?: Proposal
  request?: Proposal['request']
  onEditAssumption?: (nextRequestText: string, oldChip: string, newChip: string) => void
  disabled?: boolean
}) {
  const request = directRequest ?? proposal?.request
  if (!request) return null
  return (
    <div className="request-card">
      <figure className="request-text" aria-label="Original request">
        <Quote size={14} aria-hidden />
        <blockquote>{request.requestText}</blockquote>
      </figure>
      <table className="data-table">
        <caption className="visually-hidden">Structured request items</caption>
        <thead>
          <tr>
            <th scope="col">Item</th>
            <th scope="col">Quantity</th>
            <th scope="col">Hard requirements</th>
          </tr>
        </thead>
        <tbody>
          {request.items.map((item) => (
            <tr key={item.itemId}>
              <th scope="row">
                {item.what}
                {item.productId && <small className="muted"> · product {item.productId}</small>}
              </th>
              <td className="num">{formatQuantity(item.quantity, item.unit)}</td>
              <td>
                {item.requirements?.length ? (
                  <ul className="chip-list">
                    {item.requirements.map((r) => (
                      <li key={`${r.kind}-${r.code}`} className="chip chip-requirement">
                        {requirementLabel(r)}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <span className="muted">None</span>
                )}
                {item.preferences?.length ? (
                  <p className="muted small">
                    Preferences: {item.preferences.map(formatPreferenceValue).join(', ')}
                  </p>
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <dl className="inline-facts">
        <div>
          <dt>Deliver by</dt>
          <dd>{formatDate(request.requestedDate)}</dd>
        </div>
        <div>
          <dt>Assumptions</dt>
          <dd>
            <AssumptionChips
              request={request}
              onEditAssumption={onEditAssumption}
              disabled={disabled}
            />
          </dd>
        </div>
      </dl>
    </div>
  )
}
