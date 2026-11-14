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

/**
 * The delivery days an order's lines arrive on ("Mon 16 Nov", "Mon 16 Nov and Tue 17 Nov"); the requested
 * date only when there are no lines. The split-order guard (T62) is keyed on these, not the requested date.
 */
export function deliveryDays(proposal: Proposal): string {
  const days = [...new Set((proposal.lines ?? []).map((line) => line.deliveryDate.slice(0, 10)))].sort()
  const names = (days.length ? days : [proposal.requestedDate]).map((day) => formatDate(day))
  return names.length <= 1 ? names.join('') : `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`
}

/**
 * T62: the limits apply to this order plus what is already committed for delivery on the same day. When this
 * order alone is within the limit, the day's committed orders took it over: say that, never "€300 is above
 * your €500 limit". Returns null when the order alone is over the limit (the usual wording applies).
 */
export function committedLimitSentence(
  code: ReasonCode,
  proposal: Proposal,
  mandate?: Mandate,
): string | null {
  if (!mandate || (code !== 'OVER_AUTO_LIMIT' && code !== 'OVER_HARD_LIMIT')) return null
  const hard = code === 'OVER_HARD_LIMIT'
  const limit = hard ? mandate.hardLimitEur : mandate.autoApproveLimitEur
  if (Number(proposal.totals.exclVat) > Number(limit)) return null
  return `${formatMoney(proposal.totals.exclVat)} excl. VAT plus the orders already committed for delivery on ${deliveryDays(proposal)} is above your ${formatMoney(limit)} ${hard ? 'hard limit' : 'auto-approve limit'}.`
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
    const committed = committedLimitSentence('OVER_AUTO_LIMIT', proposal, mandate)
    if (committed) return `${committed} Everything else is verified or declared.`
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

/** VALIO_SUBSTITUTE in plain words: which line Valio Aimo lists as the substitute for what. */
function valioSubstituteStatement(proposal: Proposal): string | null {
  const parts = proposal.lines
    .filter(
      (line) =>
        line.substitutionSource === 'VALIO' || (line.reasons ?? []).includes('VALIO_SUBSTITUTE'),
    )
    .map((line) => {
      const item = proposal.request.items.find((i) => i.itemId === line.itemId)
      const original = line.substituteFor ?? item?.productId ?? item?.what
      return original ? `${line.name} for ${original}` : line.name
    })
  return parts.length ? `Listed by Valio Aimo as a substitute: ${parts.join('; ')}.` : null
}

/**
 * The reason in plain language, filled from the registry template where the values are known.
 * `null` when there is nothing to say beyond the chip itself, so a chip is never followed by its own
 * label ("Agent substitute Agent substitute").
 */
export function plainReason(
  code: ReasonCode,
  proposal: Proposal,
  mandate?: Mandate,
): string | null {
  if (code === 'AGENT_SUBSTITUTE') return substituteStatement(proposal)
  if (code === 'VALIO_SUBSTITUTE') return valioSubstituteStatement(proposal)
  if (code === 'OVER_AUTO_LIMIT' && mandate) {
    return (
      committedLimitSentence(code, proposal, mandate) ??
      `${formatMoney(proposal.totals.exclVat)} is above your ${formatMoney(mandate.autoApproveLimitEur)} auto-approve limit.`
    )
  }
  const text = reasonText(code, { total: formatMoney(proposal.totals.exclVat) })
  if (/\{\w+\}/.test(text)) return null
  return text.trim().toLowerCase() === reasonChip(code).trim().toLowerCase() ? null : text
}

/** Each reason code once, in first-seen order: a chip is never shown twice. */
export function uniqueReasons(codes: readonly ReasonCode[]): ReasonCode[] {
  return [...new Set(codes)]
}

/** Only approvable trade-offs (P2.2) are ever shown for sign-off; INFO codes are context. */
export function approvalReasons(proposal: Proposal): ReasonCode[] {
  return uniqueReasons(proposal.reasons.filter((code) => REASON_REGISTRY[code].approvable))
}

export function contextReasons(proposal: Proposal): ReasonCode[] {
  return uniqueReasons(proposal.reasons.filter((code) => REASON_REGISTRY[code].effect === 'INFO'))
}

/** The product names of a proposal for a queue row: the first two, then "+N". */
export function productSummary(proposal: Proposal): string {
  const names = [...new Set(proposal.lines.map((line) => line.name))]
  return names.length <= 2
    ? names.join(', ')
    : `${names.slice(0, 2).join(', ')} +${names.length - 2}`
}

/** One statement per queue row: what approving means, else the first reason in plain words. */
export function rowStatement(proposal: Proposal, mandate?: Mandate): string | null {
  return (
    formatApprovalStatement(proposal, mandate) ??
    approvalReasons(proposal)
      .map((code) => plainReason(code, proposal, mandate))
      .find((text): text is string => Boolean(text)) ??
    null
  )
}
