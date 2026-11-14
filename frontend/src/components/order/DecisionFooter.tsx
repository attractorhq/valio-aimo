import { Ban, CheckCircle2, Hourglass, Info, ShieldCheck } from 'lucide-react'
import { REASON_REGISTRY, type Mandate, type Proposal, type ReasonCode } from '../../lib/contracts'
import { formatMoney, reasonChip, reasonText } from '../../lib/format'
import {
  decisiveEvidence,
  evidenceCountsText,
  evidenceHeadline,
  proposalRequirementChecks,
  requirementsStatement,
} from '../../lib/requirements'
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
): string | null {
  if (!statement.allProven && proposal.decision !== 'BLOCK') {
    return `${statement.clause}: not orderable as it stands.`
  }
  if (proposal.decision === 'AUTO') return `${statement.clause}.`
  if (proposal.decision === 'ASK' && proposal.state === 'CONFIRMED_SIMULATED') {
    return 'Re-checked live before the simulated confirmation.'
  }
  if (proposal.decision === 'ASK' && proposal.state !== 'PENDING_APPROVAL') return null
  if (proposal.decision === 'ASK') {
    // T83: the headline already names the approver; one line says what is proven and where sign-off happens.
    return `${statement.clause}. ${approver ?? 'The approver'} signs off v${proposal.offerVersion} in the Approver panel.`
  }
  return null // BLOCK: the headline and the reasons say it.
}

/** After the approver acted on the separate channel, the agent view shows the outcome, never a control. */
function askStateText(proposal: Proposal, approver: string): string {
  switch (proposal.state) {
    case 'CONFIRMED_SIMULATED':
      return `Approved by ${approver} → Confirmed (simulated)`
    case 'REJECTED':
      return `Rejected by ${approver} · nothing ordered`
    case 'EXPIRED':
      return 'Offer expired · re-quote needed'
    case 'SUPERSEDED':
      return 'Superseded by a new version'
    default:
      return `In progress (${proposal.state})`
  }
}

/**
 * Step 4 Decision footer: reason chips, plain-language explanation, "Waiting for Head Chef" with NO approve button,
 * and "Simulated order — mock environment" (P6 P0-2/P0-3, P10 S2).
 */
export function DecisionFooter({ proposal, mandate }: { proposal: Proposal; mandate?: Mandate }) {
  const decision = proposal.decision
  const approver = proposal.approverRole ?? mandate?.approver.role ?? 'Head Chef'
  const confirmed = proposal.state === 'CONFIRMED_SIMULATED'
  const voided =
    decision === 'ASK' && ['REJECTED', 'EXPIRED', 'SUPERSEDED'].includes(proposal.state)
  const Icon = confirmed ? CheckCircle2 : decision === 'ASK' && !voided ? Hourglass : Ban
  const tone = confirmed ? 'auto' : voided ? 'block' : decision.toLowerCase()
  const statement = requirementsStatement(
    proposalRequirementChecks(proposal, mandate?.forbiddenAllergens),
  )
  const summary = decisionSummary(proposal, statement, approver)
  // T78: the AUTO headline follows the weakest decisive level and shows the counts.
  const evidence = decisiveEvidence(proposal, mandate?.forbiddenAllergens)

  return (
    <div className={`outcome outcome-${tone}`}>
      <p className="outcome-headline">
        <Icon size={22} aria-hidden />
        <span>
          <span className="outcome-word">{decision}</span>
          {decision === 'AUTO' &&
            (statement.allProven ? evidenceHeadline(evidence) : 'A hard requirement is not proven.')}
          {decision === 'ASK' &&
            (proposal.state === 'PENDING_APPROVAL'
              ? `Waiting for ${approver}`
              : askStateText(proposal, approver))}
          {decision === 'BLOCK' && 'This order breaks the mandate.'}
        </span>
      </p>

      {decision === 'AUTO' && (
        <p className="mt-1 text-xs font-semibold" data-testid="evidence-counts">
          {evidenceCountsText(evidence)}
        </p>
      )}

      {summary && (
        <p className="mt-2 text-sm" data-testid="decision-summary">
          {summary}
        </p>
      )}

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

      <p className="simulation-notice">
        <ShieldCheck size={14} aria-hidden />
        {proposal.simulationNotice ?? 'Simulated order — mock environment'}
      </p>
    </div>
  )
}
