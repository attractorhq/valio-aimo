import { Lock, UserRound } from 'lucide-react'
import type { Mandate } from '../../lib/contracts'
import { allergenName, formatMoney } from '../../lib/format'

function yesNo(value: boolean, yes: string, no: string) {
  return value ? yes : no
}

/** Mandate picker plus a read-only view of the selected mandate. Agents can read it, never change it. */
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
      <fieldset className="mandate-picker" disabled={disabled}>
        <legend className="visually-hidden">Choose the buyer organisation</legend>
        {mandates.map((mandate, index) => (
          <label
            key={mandate.customerId}
            className={`mandate-option ${mandate.customerId === selected?.customerId ? 'selected' : ''}`}
          >
            <input
              type="radio"
              name="mandate"
              value={mandate.customerId}
              checked={mandate.customerId === selected?.customerId}
              onChange={() => onSelect(mandate.customerId)}
            />
            <span className="mandate-key">M{index + 1}</span>
            <span>
              <strong>{mandate.organisationName}</strong>
              <small>{mandate.segment}</small>
            </span>
          </label>
        ))}
      </fieldset>
      {selected && (
        <div className="mandate-details">
          <p className="mandate-readonly">
            <Lock size={13} aria-hidden /> Read-only · version {selected.version} · set by the buyer
            admin
          </p>
          <dl>
            <div>
              <dt>Auto-approve up to</dt>
              <dd>{formatMoney(selected.autoApproveLimitEur)} excl. VAT</dd>
            </div>
            <div>
              <dt>Hard limit</dt>
              <dd>{formatMoney(selected.hardLimitEur)} excl. VAT</dd>
            </div>
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
          <p className="mandate-approver">
            <UserRound size={15} aria-hidden />
            <span>
              Approver: <strong>{selected.approver.role}</strong>
            </span>
          </p>
        </div>
      )}
    </section>
  )
}
