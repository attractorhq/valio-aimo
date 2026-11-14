import { Check, Lock } from 'lucide-react'
import type { Mandate } from '../../lib/contracts'
import { allergenName, formatMoney } from '../../lib/format'

function yesNo(value: boolean, yes: string, no: string) {
  return value ? yes : no
}

/**
 * Buyer picker (a segmented control: filled background + check, no side bar) and the selected buyer's
 * mandate, read-only. The key limits are always visible; every other rule is one click away in Details.
 */
export function MandatePanel({
  mandates,
  selected,
  onSelect,
  disabled,
}: {
  mandates: Mandate[]
  selected?: Mandate
  onSelect: (customerId: string) => void
  disabled?: boolean
}) {
  return (
    <section className="panel mandate-panel" aria-labelledby="mandate-heading">
      <h2 id="mandate-heading" className="panel-title">
        Buyer mandate
      </h2>
      <fieldset className="segmented" disabled={disabled}>
        <legend className="visually-hidden">Choose the buyer organisation</legend>
        {mandates.map((mandate) => {
          const isSelected = mandate.customerId === selected?.customerId
          return (
            <label
              key={mandate.customerId}
              className={`segment ${isSelected ? 'is-selected' : ''}`}
            >
              <input
                type="radio"
                name="mandate"
                className="segment-input"
                value={mandate.customerId}
                checked={isSelected}
                onChange={() => onSelect(mandate.customerId)}
              />
              <span className="segment-check" aria-hidden>
                {isSelected && <Check size={14} />}
              </span>
              <span className="segment-text">
                <strong>{mandate.organisationName}</strong>
                <small>{mandate.segment}</small>
              </span>
            </label>
          )
        })}
      </fieldset>
      {selected && (
        <div className="mandate-details">
          <dl className="key-limits">
            <div>
              <dt>Auto-approve up to</dt>
              <dd>{formatMoney(selected.autoApproveLimitEur)} excl. VAT</dd>
            </div>
            <div>
              <dt>Hard limit</dt>
              <dd>{formatMoney(selected.hardLimitEur)} excl. VAT</dd>
            </div>
            <div>
              <dt>Approver</dt>
              <dd>{selected.approver.role}</dd>
            </div>
          </dl>
          <details className="more">
            <summary>
              <span>Details</span>
              <span className="more-meta">
                <Lock size={12} aria-hidden /> Read-only · v{selected.version}
              </span>
            </summary>
            <dl className="rule-list">
              <div>
                <dt>Allergen mode</dt>
                <dd>
                  {selected.allergenMode === 'STRICT'
                    ? 'Strict: every product needs an allergen declaration'
                    : 'Standard'}
                </dd>
              </div>
              <div>
                <dt>Valio Aimo substitutes</dt>
                <dd>
                  {yesNo(
                    selected.autoAcceptValioSubstitutes,
                    'Accepted automatically if compliant',
                    'Need approval',
                  )}
                </dd>
              </div>
              <div>
                <dt>Agent substitutes</dt>
                <dd>Always need approval</dd>
              </div>
              <div>
                <dt>Supplier-warehouse stock</dt>
                <dd>
                  {yesNo(
                    selected.autoAcceptSupplierStock,
                    'Accepted automatically',
                    'Needs approval',
                  )}
                </dd>
              </div>
              <div>
                <dt>Minimum delivery reliability</dt>
                <dd>{Math.round(Number(selected.minReliability) * 100)} %</dd>
              </div>
              <div>
                <dt>Order cut-offs</dt>
                <dd>{selected.enforceCutoff ? 'Enforced' : 'Not enforced'}</dd>
              </div>
              <div>
                <dt>Forbidden allergens</dt>
                <dd>
                  {selected.forbiddenAllergens?.length
                    ? selected.forbiddenAllergens.map(allergenName).join(', ')
                    : 'None'}
                </dd>
              </div>
            </dl>
            <p className="muted small">
              Set by the buyer admin. The agent can read it, never change it.
            </p>
          </details>
        </div>
      )}
    </section>
  )
}
