import { REASON_REGISTRY, type Mandate, type Proposal, type ReasonCode } from '../../lib/contracts'
import { formatDate, formatMoney, formatNumber, reasonChip, reasonText } from '../../lib/format'
import {
  formatFullSplitApprovalStatement,
  formatSplitDetailLines,
} from '../proposal/SplitProposalSummary'

/** 'a', 'a and b', 'a, b and c'. */
function joinAnd(items: string[]): string {
  if (items.length <= 1) return items.join('')
  return `${items.slice(0, -1).join(', ')} and ${items[items.length - 1]}`
}

const WEEKDAY_NAMES = [
  'Sunday',
  'Monday',
  'Tuesday',
  'Wednesday',
  'Thursday',
  'Friday',
  'Saturday',
] as const

const DEFAULT_MANDATE_LIMITS: Record<
  string,
  { autoApproveLimitEur: string; hardLimitEur: string; minReliability: string }
> = {
  'nordic-harbour-hotels': {
    autoApproveLimitEur: '1500.00',
    hardLimitEur: '5000.00',
    minReliability: '0.80',
  },
  'mock-hotels': {
    autoApproveLimitEur: '1500.00',
    hardLimitEur: '5000.00',
    minReliability: '0.80',
  },
  'northbay-school-kitchen': {
    autoApproveLimitEur: '500.00',
    hardLimitEur: '3000.00',
    minReliability: '0.80',
  },
  'mock-school': {
    autoApproveLimitEur: '500.00',
    hardLimitEur: '3000.00',
    minReliability: '0.80',
  },
}

function parseIsoDay(iso: string): Date {
  const [y, m, d] = iso.slice(0, 10).split('-').map(Number)
  return new Date(Date.UTC(y, m - 1, d))
}

/**
 * The delivery days an order's lines arrive on ("Mon 16 Nov", "Mon 16 Nov and Tue 17 Nov"); the requested
 * date only when there are no lines. The split-order guard (T62) is keyed on these, not the requested date.
 */
function deliveryDays(proposal: Proposal): string {
  const days = [...new Set(proposal.lines.map((line) => line.deliveryDate.slice(0, 10)))].sort()
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
  omitTotalAmount = false,
): string | null {
  if (code !== 'OVER_AUTO_LIMIT' && code !== 'OVER_HARD_LIMIT') return null
  const hard = code === 'OVER_HARD_LIMIT'
  const limit = hard ? resolveHardLimitEur(proposal, mandate) : resolveAutoLimitEur(proposal, mandate)
  if (!limit || Number(proposal.totals.exclVat) > Number(limit)) return null
  const order = omitTotalAmount ? 'this order' : `${formatMoney(proposal.totals.exclVat)} excl. VAT`
  const threshold = hard ? 'hard limit' : 'auto-approve limit'
  const sentence = `${order} plus the orders already committed for delivery on ${deliveryDays(proposal)} exceeds your ${formatMoney(limit)} ${threshold}.`
  return hard ? `${sentence} No agent or approver can override this.` : sentence
}

function resolveAutoLimitEur(proposal: Proposal, mandate?: Mandate): string | undefined {
  return (
    mandate?.autoApproveLimitEur ?? DEFAULT_MANDATE_LIMITS[proposal.customerId]?.autoApproveLimitEur
  )
}

function resolveHardLimitEur(proposal: Proposal, mandate?: Mandate): string | undefined {
  return mandate?.hardLimitEur ?? DEFAULT_MANDATE_LIMITS[proposal.customerId]?.hardLimitEur
}

function resolveMinReliability(proposal: Proposal, mandate?: Mandate): string | undefined {
  return mandate?.minReliability ?? DEFAULT_MANDATE_LIMITS[proposal.customerId]?.minReliability
}

function latestDeliveryIso(proposal: Proposal): string | undefined {
  if (proposal.lines.length === 0) return undefined
  const later = proposal.lines.find((l) => l.deliveryDate > proposal.requestedDate)
  if (later) {
    return proposal.lines.reduce(
      (max, l) => (l.deliveryDate > max ? l.deliveryDate : max),
      later.deliveryDate,
    )
  }
  return proposal.lines[0].deliveryDate
}

function extractReliabilityPercent(proposal: Proposal): string | undefined {
  for (const line of proposal.lines) {
    for (const fact of line.facts ?? []) {
      if (fact.fact !== 'reliability') continue
      if (typeof fact.value === 'number' && Number.isFinite(fact.value)) {
        const pct = fact.value <= 1 ? Math.round(fact.value * 1000) / 10 : Math.round(fact.value * 10) / 10
        return `${pct} %`
      }
      if (typeof fact.value === 'string' && fact.value.trim() !== '') {
        const num = Number(fact.value)
        if (Number.isFinite(num)) {
          const pct = num <= 1 ? Math.round(num * 1000) / 10 : Math.round(num * 10) / 10
          return `${pct} %`
        }
      }
      const match = (fact.display ?? '').match(/(\d+(?:[.,]\d+)?\s*%)/)
      if (match) return match[1].replace(/\s+/g, ' ')
    }
  }
  return undefined
}

function partialAvailabilityReasonText(proposal: Proposal): string {
  const deliveredByItem = new Map<string, number>()
  const linesByItem = new Map<string, Proposal['lines']>()
  for (const line of proposal.lines) {
    deliveredByItem.set(
      line.itemId,
      (deliveredByItem.get(line.itemId) ?? 0) + Number(line.deliveredQuantity.value),
    )
    const group = linesByItem.get(line.itemId) ?? []
    group.push(line)
    linesByItem.set(line.itemId, group)
  }

  const parts: string[] = []
  for (const item of proposal.request.items) {
    const deliv = deliveredByItem.get(item.itemId) ?? 0
    const req = Number(item.quantity)
    const itemLines = linesByItem.get(item.itemId) ?? []
    if (deliv > 0 && deliv < req) {
      parts.push(
        `Only ${formatNumber(String(deliv))} ${item.unit} of ${item.what} in stock; ${formatNumber(String(req))} ${item.unit} needed.`,
      )
    } else if (itemLines.length >= 2) {
      const first = itemLines[0]
      const restNames = itemLines
        .slice(1)
        .map((l) => l.name)
        .join(' + ')
      parts.push(
        reasonText('PARTIAL_AVAILABILITY', {
          available: `${formatNumber(first.deliveredQuantity.value)} ${first.deliveredQuantity.unit}`,
          needed: `${formatNumber(item.quantity)} ${item.unit}`,
          otherProduct: restNames,
        }),
      )
    }
  }

  if (parts.length > 0) return parts.join(' ')
  return 'Available stock covers only part of the requested quantity.'
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

  const splitStatement = formatFullSplitApprovalStatement(proposal)
  if (splitStatement) {
    return splitStatement
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
  // One clause per requested item, listing every substitute line for it (T97: never repeat "you asked for").
  const byItem = new Map<string, { what: string; names: string[] }>()
  for (const line of proposal.lines) {
    const item = proposal.request.items.find((i) => i.itemId === line.itemId)
    if (!item) continue
    const isAgentSubstitute =
      line.substitutionSource === 'AGENT' ||
      (line.reasons ?? []).includes('AGENT_SUBSTITUTE') ||
      (item.productId != null &&
        item.productId !== line.productId &&
        line.substitutionSource !== 'VALIO')
    if (!isAgentSubstitute) continue
    const group = byItem.get(item.itemId) ?? { what: item.what, names: [] }
    if (!group.names.includes(line.name)) group.names.push(line.name)
    byItem.set(item.itemId, group)
  }
  const parts = [...byItem.values()].map(({ what, names }) =>
    names.length === 1
      ? `you asked for “${what}”; this line is ${names[0]}`
      : `you asked for “${what}”; these lines are ${joinAnd(names)}`,
  )
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
  if (code === 'SPLIT') {
    const details = formatSplitDetailLines(proposal)
    return details.length > 0
      ? details.join(' ')
      : `Covered by ${proposal.lines.length} products that all meet your requirements.`
  }
  if (code === 'AGENT_SUBSTITUTE') return substituteStatement(proposal)
  if (code === 'VALIO_SUBSTITUTE') return valioSubstituteStatement(proposal)
  const committed = committedLimitSentence(code, proposal, mandate)
  if (committed) return committed
  if (code === 'OVER_AUTO_LIMIT') {
    const autoLimit = resolveAutoLimitEur(proposal, mandate)
    if (mandate) {
      return `${formatMoney(proposal.totals.exclVat)} is above your ${formatMoney(mandate.autoApproveLimitEur)} auto-approve limit.`
    }
    if (autoLimit) {
      return `${formatMoney(proposal.totals.exclVat)} is above your ${formatMoney(autoLimit)} auto-approve limit.`
    }
    return `${formatMoney(proposal.totals.exclVat)} excl. VAT is above the buyer's auto-approve limit.`
  }
  if (code === 'OVER_HARD_LIMIT') {
    const hardLimit = resolveHardLimitEur(proposal, mandate)
    if (hardLimit) {
      return reasonText(code, {
        total: formatMoney(proposal.totals.exclVat),
        limit: formatMoney(hardLimit),
      })
    }
    return `${formatMoney(proposal.totals.exclVat)} excl. VAT is above the buyer's hard limit. No agent or approver can override this.`
  }
  if (code === 'DATE_LATER') {
    const delivIso = latestDeliveryIso(proposal)
    if (delivIso) {
      return reasonText('DATE_LATER', {
        availableDate: formatDate(delivIso),
        requestedDate: formatDate(proposal.requestedDate),
      })
    }
    return `Earliest available delivery is later than your requested date (${formatDate(proposal.requestedDate)}).`
  }
  if (code === 'PARTIAL_AVAILABILITY') {
    return partialAvailabilityReasonText(proposal)
  }
  if (code === 'LOW_RELIABILITY') {
    const pct = extractReliabilityPercent(proposal)
    if (pct) {
      return reasonText('LOW_RELIABILITY', { pct })
    }
    const minRel = resolveMinReliability(proposal, mandate)
    if (minRel) {
      return `Historical delivery reliability is below your ${Math.round(Number(minRel) * 100)} % mandate threshold.`
    }
    return 'Historical delivery reliability is below the buyer mandate threshold.'
  }
  if (code === 'REQUOTED') {
    return 'Re-checked live against current price, stock, cut-off and mandate before ordering.'
  }
  const text = reasonText(code, { total: formatMoney(proposal.totals.exclVat) })
  if (/\{\w+\}/.test(text)) return null
  return text.trim().toLowerCase() === reasonChip(code).trim().toLowerCase() ? null : text
}

/**
 * Plain-language clauses explaining each reason why a proposal was not automatically approved (T86).
 */
export function explainWhyNotAuto(
  proposal: Proposal,
  mandate?: Mandate,
  options?: { omitTotalAmount?: boolean },
): string {
  const codes = approvalReasons(proposal)
  const clauses: string[] = []

  for (const code of codes) {
    if (code === 'OVER_AUTO_LIMIT') {
      const committed = committedLimitSentence(code, proposal, mandate, options?.omitTotalAmount)
      if (committed) {
        clauses.push(committed.slice(0, -1))
        continue
      }
      const limit = resolveAutoLimitEur(proposal, mandate)
      if (options?.omitTotalAmount) {
        clauses.push(
          limit
            ? `the order total exceeds your ${formatMoney(limit)} auto-approve limit`
            : 'the order total exceeds the auto-approve limit',
        )
      } else {
        clauses.push(
          limit
            ? `the order total (${formatMoney(proposal.totals.exclVat)} excl. VAT) is above your ${formatMoney(limit)} auto-approve limit`
            : `the order total (${formatMoney(proposal.totals.exclVat)} excl. VAT) is above the auto-approve limit`,
        )
      }
      continue
    }
    if (code === 'DATE_LATER') {
      const delivIso = latestDeliveryIso(proposal)
      clauses.push(
        delivIso
          ? `earliest delivery is ${formatDate(delivIso)} instead of requested ${formatDate(proposal.requestedDate)}`
          : `earliest delivery is later than requested ${formatDate(proposal.requestedDate)}`,
      )
      continue
    }
    if (code === 'PARTIAL_AVAILABILITY') {
      const deliveredByItem = new Map<string, number>()
      for (const line of proposal.lines) {
        deliveredByItem.set(
          line.itemId,
          (deliveredByItem.get(line.itemId) ?? 0) + Number(line.deliveredQuantity.value),
        )
      }
      const itemShortfalls: string[] = []
      for (const item of proposal.request.items) {
        const deliv = deliveredByItem.get(item.itemId) ?? 0
        const req = Number(item.quantity)
        if (deliv > 0 && deliv < req) {
          itemShortfalls.push(
            `only ${formatNumber(String(deliv))} ${item.unit} of the requested ${formatNumber(String(req))} ${item.unit} of ${item.what} is available in stock`,
          )
        }
      }
      if (itemShortfalls.length > 0) {
        clauses.push(itemShortfalls.join(' and '))
      } else {
        clauses.push('available stock for the primary product covers only part of the requested quantity')
      }
      continue
    }
    if (code === 'AGENT_SUBSTITUTE') {
      const sub = substituteStatement(proposal)
      if (sub) {
        const detail = sub.replace(/^Not exactly what you asked for:\s*/i, '').replace(/\.$/, '')
        clauses.push(`a substitute product was selected (${detail})`)
      } else {
        clauses.push('a substitute product was selected instead of the exact requested item')
      }
      continue
    }
    if (code === 'VALIO_SUBSTITUTE') {
      const sub = valioSubstituteStatement(proposal)
      if (sub) {
        const detail = sub
          .replace(/^Listed by Valio Aimo as a substitute:\s*/i, '')
          .replace(/\.$/, '')
        clauses.push(`a Valio Aimo-listed substitute was selected (${detail})`)
      } else {
        clauses.push('a Valio Aimo-listed substitute product was selected')
      }
      continue
    }
    if (code === 'SUPPLIER_STOCK_UNKNOWN') {
      clauses.push("stock ships from the supplier's warehouse with an unverified quantity")
      continue
    }
    if (code === 'QUANTITY_BASIS_WEAK') {
      clauses.push('pack size was estimated from the product name')
      continue
    }
    if (code === 'LOW_RELIABILITY') {
      const pct = extractReliabilityPercent(proposal)
      clauses.push(
        pct
          ? `historical delivery reliability (${pct}) is below the mandate threshold`
          : 'historical delivery reliability is below the mandate threshold',
      )
      continue
    }
  }

  if (clauses.length === 0) {
    return 'a mandate trade-off requires human sign-off'
  }
  if (clauses.length === 1) {
    return clauses[0]
  }
  if (clauses.length === 2) {
    return `${clauses[0]} and ${clauses[1]}`
  }
  return `${clauses.slice(0, -1).join(', ')}, and ${clauses[clauses.length - 1]}`
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
