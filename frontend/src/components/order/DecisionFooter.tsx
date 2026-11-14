import { Ban, CheckCircle2, Hourglass, Info, ShieldCheck } from 'lucide-react'
import { REASON_REGISTRY, type Mandate, type Proposal, type ReasonCode } from '../../lib/contracts'
import { formatMoney, reasonChip, reasonText } from '../../lib/format'
import { proposalRequirementChecks, requirementsStatement } from '../../lib/requirements'
import { plainReason, uniqueReasons } from '../approver/explain'

/** The reason in plain words, or null when that would only repeat the chip (T45). */
function explainReason(code: ReasonCode, proposal: Proposal, mandate?: Mandate): string | null {
  const params: Record<string, string> = { total: formatMoney(proposal.totals.exclVat) }
  if (mandate) {
    params.limit = formatMoney(
      code === 'OVER_HARD_LIMIT' ? mandate.hardLimitEur : mandate.autoApproveLimitEur,
    )
  }
  const text = reasonText(code, params)
  return /\{\w+\}/.test(text) ? plainReason(code, proposal, mandate) : text
}

/**
 * The "proven" statement is derived from the same requirement facts the trust cards show (T41): it is
 * made only when every hard requirement on every line is proven by its evidence.
 */
function decisionSummary(
  proposal: Proposal,
  statement: ReturnType<typeof requirementsStatement>,
  approver?: string,
): string {
  if (!statement.allProven && proposal.decision !== 'BLOCK') {
    return `${statement.clause}, so this proposal must not be ordered as it stands.`
  }
  if (proposal.decision === 'AUTO') {
    return `${statement.clause}; stock and delivery terms are verified or declared, and the net total is within the buyer mandate.`
  }
  if (proposal.decision === 'ASK' && proposal.state === 'CONFIRMED_SIMULATED') {
    return `${approver ?? 'The approver'} approved this exact version in the Approver panel; the gateway re-checked it before the simulated confirmation.`
  }
  if (proposal.decision === 'ASK' && proposal.state !== 'PENDING_APPROVAL') {
    return 'This proposal version will not be ordered.'
  }
  if (proposal.decision === 'ASK') {
    return `${statement.clause}; this proposal version waits for ${approver ?? 'Head Chef (demo)'} to sign off the trade-off below.`
  }
  return 'Deterministic gateway policy blocked this proposal because it breaches a non-overridable mandate rule.'
}

/** After the approver acted on the separate channel, the agent view shows the outcome, never a control. */
function askStateText(proposal: Proposal, approver: string): string {
  switch (proposal.state) {
    case 'CONFIRMED_SIMULATED':
      return `Approved by ${approver} → Confirmed (simulated)`
    case 'REJECTED':
      return `Rejected by ${approver}. Nothing was ordered.`
    case 'EXPIRED':
      return 'The offer expired before sign-off. Re-quote needed.'
    case 'SUPERSEDED':
      return 'Superseded: a new version was decided from scratch.'
    default:
      return `In progress (${proposal.state})`
  }
}

/**
 * Step 4 Decision footer: reason chips, plain-language explanation, "Waiting for Head Chef (demo)" with NO approve button,
 * and "Simulated order — mock environment" (P6 P0-2/P0-3, P10 S2).
 */
export function DecisionFooter({ proposal, mandate }: { proposal: Proposal; mandate?: Mandate }) {
  const decision = proposal.decision
  const approver = proposal.approverRole ?? mandate?.approver.role ?? 'Head Chef (demo)'
  const confirmed = proposal.state === 'CONFIRMED_SIMULATED'
  const voided =
    decision === 'ASK' && ['REJECTED', 'EXPIRED', 'SUPERSEDED'].includes(proposal.state)
  const Icon = confirmed ? CheckCircle2 : decision === 'ASK' && !voided ? Hourglass : Ban
  const tone = confirmed ? 'auto' : voided ? 'block' : decision.toLowerCase()
  const statement = requirementsStatement(
    proposalRequirementChecks(proposal, mandate?.forbiddenAllergens),
  )

  return (
    <div className={`outcome outcome-${tone}`}>
      <p className="outcome-headline">
        <Icon size={22} aria-hidden />
        <span>
          <span className="outcome-word">{decision}</span>
          {decision === 'AUTO' &&
            (statement.allProven
              ? 'Every fact is proven and the order is inside the mandate.'
              : 'A hard requirement is not proven.')}
          {decision === 'ASK' &&
            (proposal.state === 'PENDING_APPROVAL'
              ? `Waiting for ${approver}`
              : askStateText(proposal, approver))}
          {decision === 'BLOCK' && 'This order breaks the mandate.'}
        </span>
      </p>

      <p className="mt-2 text-sm" data-testid="decision-summary">
        {decisionSummary(proposal, statement, approver)}
      </p>

      {proposal.reasons.length > 0 && (
        <ul className="reason-list" aria-label="Reasons">
          {uniqueReasons(proposal.reasons).map((code) => {
            const effect = REASON_REGISTRY[code].effect
            const text = explainReason(code, proposal, mandate)
            return (
              <li key={code} className={`reason reason-${effect.toLowerCase()}`}>
                <span className="chip">
                  {effect === 'INFO' ? <Info size={13} aria-hidden /> : null}
                  {reasonChip(code)}
                </span>
                {text && <span>{text}</span>}
              </li>
            )
          })}
        </ul>
      )}

      {decision === 'ASK' && proposal.state === 'PENDING_APPROVAL' && (
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
