import { REASON_REGISTRY, type Mandate, type Proposal, type ReasonCode } from '../../lib/contracts'
import { formatMoney, reasonChip, reasonText } from '../../lib/format'

/** The reason in plain language, filled from the registry template where the values are known. */
export function plainReason(code: ReasonCode, proposal: Proposal, mandate?: Mandate): string {
  if (code === 'OVER_AUTO_LIMIT' && mandate) {
    return `${formatMoney(proposal.totals.exclVat)} is above your ${formatMoney(mandate.autoApproveLimitEur)} auto-approve limit.`
  }
  const text = reasonText(code, { total: formatMoney(proposal.totals.exclVat) })
  return /\{\w+\}/.test(text) ? reasonChip(code) : text
}

/** Only approvable trade-offs (P2.2) are ever shown for sign-off; INFO codes are context. */
export function approvalReasons(proposal: Proposal): ReasonCode[] {
  return proposal.reasons.filter((code) => REASON_REGISTRY[code].approvable)
}

export function contextReasons(proposal: Proposal): ReasonCode[] {
  return proposal.reasons.filter((code) => REASON_REGISTRY[code].effect === 'INFO')
}
