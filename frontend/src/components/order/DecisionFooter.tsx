import { Ban, CheckCircle2, Hourglass, Info, ShieldCheck } from 'lucide-react'
import { REASON_REGISTRY, type Mandate, type Proposal, type ReasonCode } from '../../lib/contracts'
import { formatMoney, reasonChip, reasonText } from '../../lib/format'

function explainReason(code: ReasonCode, proposal: Proposal, mandate?: Mandate): string {
  const params: Record<string, string> = { total: formatMoney(proposal.totals.exclVat) }
  if (mandate) {
    params.limit = formatMoney(
      code === 'OVER_HARD_LIMIT' ? mandate.hardLimitEur : mandate.autoApproveLimitEur,
    )
  }
  const text = reasonText(code, params)
  return /\{\w+\}/.test(text) ? reasonChip(code) : text
}

function decisionSummary(proposal: Proposal, approver?: string): string {
  if (proposal.decision === 'AUTO') {
    return 'All hard requirements, stock and delivery terms are verified or declared, and the net total is within the buyer mandate.'
  }
  if (proposal.decision === 'ASK') {
    return `Every hard requirement is proven; this proposal version waits for ${approver ?? 'Head Chef (demo)'} to sign off the trade-off below.`
  }
  return 'Deterministic gateway policy blocked this proposal because it breaches a non-overridable mandate rule.'
}

/**
 * Step 4 Decision footer: reason chips, plain-language explanation, "Waiting for Head Chef (demo)" with NO approve button,
 * and "Simulated order — mock environment" (P6 P0-2/P0-3, P10 S2).
 */
export function DecisionFooter({
  proposal,
  mandate,
}: {
  proposal: Proposal
  mandate?: Mandate
}) {
  const decision = proposal.decision
  const approver = proposal.approverRole ?? mandate?.approver.role ?? 'Head Chef (demo)'
  const Icon = decision === 'AUTO' ? CheckCircle2 : decision === 'ASK' ? Hourglass : Ban

  return (
    <div className={`outcome outcome-${decision.toLowerCase()}`}>
      <p className="outcome-headline">
        <Icon size={22} aria-hidden />
        <span>
          <span className="outcome-word">{decision}</span>
          {decision === 'AUTO' && 'Every fact is proven and the order is inside the mandate.'}
          {decision === 'ASK' && `Waiting for ${approver}`}
          {decision === 'BLOCK' && 'This order breaks the mandate.'}
        </span>
      </p>

      <p className="mt-2 text-sm">{decisionSummary(proposal, approver)}</p>

      {proposal.reasons.length > 0 && (
        <ul className="reason-list" aria-label="Reasons">
          {proposal.reasons.map((code) => {
            const effect = REASON_REGISTRY[code].effect
            return (
              <li key={code} className={`reason reason-${effect.toLowerCase()}`}>
                <span className="chip">
                  {effect === 'INFO' ? <Info size={13} aria-hidden /> : null}
                  {reasonChip(code)}
                </span>
                <span>{explainReason(code, proposal, mandate)}</span>
              </li>
            )
          })}
        </ul>
      )}

      {decision === 'ASK' && (
        <p className="muted small">
          The agent cannot approve. {approver} signs off version {proposal.offerVersion} of this
          proposal in the separate Approver panel.
        </p>
      )}

      <p className="simulation-notice">
        <ShieldCheck size={14} aria-hidden />
        {proposal.simulationNotice ?? 'Simulated order — mock environment'}
      </p>
    </div>
  )
}
