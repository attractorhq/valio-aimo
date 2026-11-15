// T109: plain one-line explanations for the buyer and the approver. Copy only: every sentence is built from
// facts already on the proposal, and a sentence whose fact is missing is left out, never guessed.
import type {
  ExcludedProduct,
  OfferLine,
  RequestItem,
  StructuredRequest,
  TrustLabel,
} from './contracts'
import { formatClock, formatDate, formatNumber, formatQuantity, requirementLabel } from './format'
import { requirementChecks, type RequirementCheck } from './requirements'

const WEEKDAY_NAMES = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']

function dayNumber(value: string): number {
  const [year, month, day] = value.slice(0, 10).split('-').map(Number)
  return Date.UTC(year, month - 1, day) / 86_400_000
}

/** "Thursday" within a week of the delivery day, else "Thu 26 Nov". */
function requestedDayName(deliveryDate: string, requestedDate: string): string {
  const gap = dayNumber(requestedDate) - dayNumber(deliveryDate)
  if (gap > 0 && gap < 7) {
    const [year, month, day] = requestedDate.slice(0, 10).split('-').map(Number)
    return WEEKDAY_NAMES[new Date(Date.UTC(year, month - 1, day)).getUTCDay()]
  }
  return formatDate(requestedDate)
}

/**
 * "Valio offers delivery on Mon 16 Nov for this product; it arrives 3 days before Thursday." Null when no line
 * arrives before the requested date. Several early lines on one day share one sentence.
 */
export function earlyDeliverySentence(
  lines: ReadonlyArray<{ deliveryDate: string }>,
  requestedDate: string,
): string | null {
  const requested = dayNumber(requestedDate)
  const early = lines.filter((line) => dayNumber(line.deliveryDate) < requested)
  if (early.length === 0) return null
  const first = early.map((line) => line.deliveryDate).sort()[0]
  const days = requested - dayNumber(first)
  const span = `${days} ${days === 1 ? 'day' : 'days'} before ${requestedDayName(first, requestedDate)}`
  const what =
    lines.length === 1
      ? 'this product'
      : early.length === lines.length
        ? 'these products'
        : `${early.length} of ${lines.length} products`
  const verb = lines.length === 1 ? 'it arrives' : 'they arrive'
  return `Valio offers delivery on ${formatDate(first)} for ${what}; ${verb} ${span}.`
}

function trimmed(value: number): string {
  return formatNumber(String(Math.round(value * 1000) / 1000))
}

/**
 * Items whose whole packs deliver more than was asked, keyed by the item's last line:
 * "201.5 kg: rounded up to whole packs (13 × 15.5 kg); 200 kg asked".
 */
export function overshootNotes(
  lines: readonly OfferLine[],
  request?: StructuredRequest,
): Map<string, string> {
  const notes = new Map<string, string>()
  for (const item of request?.items ?? []) {
    const itemLines = lines.filter((line) => line.itemId === item.itemId)
    if (itemLines.length === 0) continue
    if (itemLines.some((line) => line.deliveredQuantity.unit !== item.unit)) continue
    const delivered = itemLines.reduce((sum, line) => sum + Number(line.deliveredQuantity.value), 0)
    const asked = Number(item.quantity)
    if (!(asked > 0) || !(delivered > asked)) continue
    const packs = itemLines.map((line) => {
      const content = Number(line.contentPerSalesUnit.value)
      const units = Number(line.quantitySalesUnits)
      const sameUnit = line.contentPerSalesUnit.unit === line.deliveredQuantity.unit
      const matches = Math.abs(content * units - Number(line.deliveredQuantity.value)) < 0.001
      // 1 kg "packs" of a product sold by the kilo say nothing; the step comes from Valio's lot size.
      return sameUnit && matches && content !== 1
        ? `${formatNumber(line.quantitySalesUnits)} × ${formatQuantity(trimmed(content), line.contentPerSalesUnit.unit)}`
        : null
    })
    const total = formatQuantity(trimmed(delivered), item.unit)
    const why = packs.every(Boolean)
      ? `rounded up to whole packs (${packs.join(' + ')})`
      : 'rounded up to the quantity step Valio sells this product in'
    notes.set(
      itemLines[itemLines.length - 1].lineId,
      `${total}: ${why}; ${formatQuantity(item.quantity, item.unit)} asked`,
    )
  }
  return notes
}

function stockFact(line: OfferLine): TrustLabel | undefined {
  return line.facts.find((fact) => fact.fact === 'stock.quantity')
}

/**
 * "Split because Valio Edam 15.5 kg has only 8 pc (124 kg) in stock." Only when the first line's verified
 * stock count is what it orders (the whole stock went to line 1); otherwise null.
 */
export function splitStockSentence(first: OfferLine): string | null {
  const fact = stockFact(first)
  if (!fact || fact.level !== 'VERIFIED' || fact.conflict) return null
  const stock = Number(fact.value)
  const ordered = Number(first.quantitySalesUnits)
  if (!Number.isFinite(stock) || stock <= 0 || ordered < stock) return null
  const delivered = formatQuantity(first.deliveredQuantity.value, first.deliveredQuantity.unit)
  const units = `${formatNumber(String(fact.value))} ${first.salesUnitLabel}`.trim()
  const amount =
    first.salesUnitLabel.trim().toLowerCase() === first.deliveredQuantity.unit
      ? delivered
      : `${units} (${delivered})`
  return `Split because ${first.name} has only ${amount} in stock.`
}

/** "verified", "stated by the supplier", "declared" (by Valio) or "not proven", for one requirement check. */
function proofWord(check: RequirementCheck): string {
  if (!check.proven || !check.fact) return 'not proven'
  if (check.fact.level === 'VERIFIED') return 'verified'
  return check.fact.source?.system === 'valio-aimo.product-data'
    ? 'stated by the supplier'
    : 'declared'
}

/** "Verified lactose-free", "Supplier states no milk (mandate)", "Lactose-free not proven". */
export function requirementPhrase(check: RequirementCheck): string {
  const label = requirementLabel(check.requirement)
  const lower = label.charAt(0).toLowerCase() + label.slice(1)
  const mandate = check.fromMandate ? ' (mandate)' : ''
  const word = proofWord(check)
  if (word === 'verified') return `Verified ${lower}${mandate}`
  if (word === 'stated by the supplier') return `Supplier states ${lower}${mandate}`
  if (word === 'declared') return `Declared ${lower}${mandate}`
  return `${label} not proven${mandate}`
}

/** "price and stock checked live 05:12", from the live price and availability facts on the line. */
export function liveCheckPhrase(line: OfferLine, mode?: 'LIVE' | 'RECORDED'): string | null {
  const price = line.facts.find((fact) => fact.fact === 'price.unit')
  const stock = stockFact(line)
  const verified = (fact?: TrustLabel) =>
    fact && fact.level === 'VERIFIED' && !fact.conflict && fact.source.retrievedAt
      ? fact
      : undefined
  const checked = [verified(price) && 'price', verified(stock) && 'stock'].filter(Boolean)
  const at = verified(price)?.source.retrievedAt ?? verified(stock)?.source.retrievedAt
  if (checked.length === 0 || !at) return null
  const how =
    mode === 'RECORDED'
      ? `checked ${formatClock(at)} (recorded data)`
      : `checked live ${formatClock(at)}`
  const extra =
    !verified(stock) && stock?.level === 'DECLARED'
      ? '; Valio does not track stock for it'
      : !verified(stock) && stock?.level === 'UNKNOWN'
        ? '; stock unknown'
        : ''
  return `${checked.join(' and ')} ${how}${extra}`
}

/** The fat % a product name states ("whipping cream 35 % 1 l" → "35"), else null. */
function fatPercentInName(name: string): string | null {
  const match = /(\d+(?:[.,]\d+)?)\s*%/.exec(name)
  return match ? match[1].replace(',', '.') : null
}

/** "fat % not stated in its product data (35 % asked)" or "35 % fat, as asked", when a fat % was asked. */
function fatClause(line: OfferLine, item?: RequestItem): string | null {
  const asked = item?.preferences?.find((pref) => pref.kind === 'FAT_PERCENT')?.value
  if (!asked) return null
  const stated = fatPercentInName(line.name)
  if (!stated) return `fat % not stated in its product data (${asked} % asked)`
  return Number(stated) === Number(asked)
    ? `its name states ${stated} % fat, as asked`
    : `its name states ${stated} % fat (${asked} % asked)`
}

function leftOutPhrase(code: string, item?: RequestItem): string {
  if (code === 'CUTOFF_PASSED') return 'past the order cut-off'
  if (code === 'CLAIM_MISSING') {
    const claims = (item?.requirements ?? [])
      .filter((req) => req.kind === 'CLAIM')
      .map((req) => requirementLabel(req).toLowerCase())
    return claims.length ? `not proven ${claims.join(', ')}` : 'a required claim not proven'
  }
  if (code.startsWith('ALLERGEN_')) return 'allergen not ruled out'
  if (code === 'NOT_AVAILABLE') return 'not deliverable in time'
  if (code === 'PRICE_UNAVAILABLE') return 'no price'
  if (code === 'SALES_BLOCKED') return 'sales blocked'
  if (code === 'SUBSTITUTE_REJECTED') return 'listed substitute breaks a requirement'
  return 'other checks failed'
}

/**
 * Why the agent picked a substitute line, in one sentence: what it proves, when it arrives, why the other
 * candidates for the item were left out, and the fat % check when the request states one. Null for a line
 * that is not a substitute.
 */
export function substituteWhySentence(
  line: OfferLine,
  item: RequestItem | undefined,
  options: {
    requestedDate: string
    excluded?: readonly ExcludedProduct[]
    forbiddenAllergens?: readonly string[]
  },
): string | null {
  const isSubstitute =
    line.substitutionSource === 'AGENT' ||
    line.substitutionSource === 'VALIO' ||
    (line.reasons ?? []).includes('AGENT_SUBSTITUTE') ||
    (line.reasons ?? []).includes('VALIO_SUBSTITUTE')
  if (!isSubstitute) return null

  const checks = requirementChecks(line, item, options.forbiddenAllergens ?? [])
  const proven = checks.filter((check) => check.proven)
  const proves = proven.length
    ? `it is ${proven
        .map(
          (check) => `${requirementLabel(check.requirement).toLowerCase()} (${proofWord(check)})`,
        )
        .join(', ')}`
    : null
  const unproven = checks.filter((check) => !check.proven)
  const gaps = unproven.length
    ? `${unproven.map((check) => requirementLabel(check.requirement).toLowerCase()).join(', ')} not proven`
    : null
  const onTime =
    dayNumber(line.deliveryDate) <= dayNumber(options.requestedDate)
      ? `arrives by ${formatDate(options.requestedDate)}`
      : null

  const valio =
    line.substitutionSource === 'VALIO' || (line.reasons ?? []).includes('VALIO_SUBSTITUTE')
  const lead = valio
    ? `Valio Aimo lists it as the substitute${line.substituteFor ? ` for ${line.substituteFor}` : ''}`
    : null

  const others = (options.excluded ?? []).filter(
    (entry) => entry.itemId === line.itemId && entry.productId !== line.productId,
  )
  const counts = new Map<string, number>()
  for (const entry of others) {
    const phrase = leftOutPhrase(entry.reasons[0] ?? '', item)
    counts.set(phrase, (counts.get(phrase) ?? 0) + 1)
  }
  const leftOut = others.length
    ? `the other ${others.length} ${others.length === 1 ? 'candidate was' : 'candidates were'} left out (${[
        ...counts.entries(),
      ]
        .sort((a, b) => b[1] - a[1])
        .slice(0, 3)
        .map(([phrase, n]) => `${n} ${phrase}`)
        .join(', ')})`
    : null

  const facts = [lead, [proves, onTime].filter(Boolean).join(' and ') || null, gaps].filter(Boolean)
  if (facts.length === 0 && !leftOut) return null
  const fat = fatClause(line, item)
  const main = facts.length ? `Chosen because ${facts.join('; ')}` : 'Chosen'
  const sentence = [leftOut ? `${main}, while ${leftOut}` : main, fat].filter(Boolean).join('; ')
  return `${sentence}.`
}
