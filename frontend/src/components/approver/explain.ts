import { REASON_REGISTRY, type Mandate, type Proposal, type ReasonCode } from '../../lib/contracts'
import { formatDate, formatMoney, formatNumber, reasonChip, reasonText } from '../../lib/format'

const WEEKDAY_NAMES = [
  'Sunday',
  'Monday',
  'Tuesday',
  'Wednesday',
  'Thursday',
  'Friday',
  'Saturday',
] as const

function parseIsoDay(iso: string): Date {
  const [y, m, d] = iso.slice(0, 10).split('-').map(Number)
  return new Date(Date.UTC(y, m - 1, d))
}

/** Plain-language summary of what approving this proposal version means (patch P8 B2, P10). */
export function formatApprovalStatement(proposal: Proposal, mandate?: Mandate): string | null {
  if (proposal.decision !== 'ASK') return null
  const reasons = new Set(proposal.reasons)

  const deliveredByItem = new Map<string, number>()
  for (const line of proposal.lines) {
    deliveredByItem.set(
      line.itemId,
      (deliveredByItem.get(line.itemId) ?? 0) + Number(line.deliveredQuantity.value),
    )
  }

  const shortfalls: string[] = []
  if (reasons.has('PARTIAL_AVAILABILITY')) {
    for (const item of proposal.request.items) {
      const deliv = deliveredByItem.get(item.itemId) ?? 0
      const req = Number(item.quantity)
      if (deliv > 0 && deliv < req) {
        shortfalls.push(
          `${formatNumber(String(deliv))} ${item.unit} instead of ${formatNumber(String(req))} ${item.unit}`,
        )
      }
    }
  }

  let dateClause: string | null = null
  if (reasons.has('DATE_LATER') && proposal.lines.length > 0) {
    const earliestIso = proposal.lines.reduce(
      (max, l) => (l.deliveryDate > max ? l.deliveryDate : max),
      proposal.lines[0].deliveryDate,
    )
    if (earliestIso > proposal.requestedDate) {
      const reqLower = proposal.request.requestText.toLowerCase()
      const refTodayIso = proposal.createdAt.slice(0, 10)
      const askedToday =
        proposal.requestedDate === refTodayIso ||
        ['today', 'tänään', 'idag'].some((w) => reqLower.includes(w))
      const reqLabel = askedToday ? 'today' : formatDate(proposal.requestedDate)
      const delivDate = parseIsoDay(earliestIso)
      const reqDate = parseIsoDay(proposal.requestedDate)
      const daysAhead = Math.round((delivDate.getTime() - reqDate.getTime()) / 86_400_000)
      const delivLabel =
        daysAhead > 0 && daysAhead <= 7
          ? WEEKDAY_NAMES[delivDate.getUTCDay()]
          : formatDate(earliestIso)
      dateClause = `on ${delivLabel} instead of ${reqLabel}`
    }
  }

  if (shortfalls.length > 0 && dateClause) {
    return `Approving means ${shortfalls.join(', ')}, ${dateClause}.`
  }
  if (shortfalls.length > 0) {
    return `Approving means ${shortfalls.join(', ')}.`
  }
  if (dateClause) {
    return `Approving means delivery ${dateClause}.`
  }
  if (proposal.reasons.length === 1 && proposal.reasons[0] === 'OVER_AUTO_LIMIT' && mandate) {
    return `${formatMoney(proposal.totals.exclVat)} is above your ${formatMoney(mandate.autoApproveLimitEur)} auto-approve limit. Everything else is verified or declared.`
  }
  return null
}

/**
 * AGENT_SUBSTITUTE in plain words: what the buyer asked for next to what each substitute line really is,
 * so the approver sees the product-type difference (e.g. a frozen fillet for "fresh salmon fillet").
 */
export function substituteStatement(proposal: Proposal): string | null {
  const parts: string[] = []
  for (const line of proposal.lines) {
    const item = proposal.request.items.find((i) => i.itemId === line.itemId)
    if (!item) continue
    const isAgentSubstitute =
      line.substitutionSource === 'AGENT' ||
      (line.reasons ?? []).includes('AGENT_SUBSTITUTE') ||
      (item.productId != null &&
        item.productId !== line.productId &&
        line.substitutionSource !== 'VALIO')
    if (isAgentSubstitute) parts.push(`you asked for “${item.what}”; this line is ${line.name}`)
  }
  return parts.length ? `Not exactly what you asked for: ${parts.join('; ')}.` : null
}

/** The reason in plain language, filled from the registry template where the values are known. */
export function plainReason(code: ReasonCode, proposal: Proposal, mandate?: Mandate): string {
  if (code === 'AGENT_SUBSTITUTE') {
    const statement = substituteStatement(proposal)
    if (statement) return statement
  }
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
