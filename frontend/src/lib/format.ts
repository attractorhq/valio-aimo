// Display formats from the contract: €1,010.00 · Mon 16 Nov · Sun 15 Nov 17:00 (Helsinki).
// Money arrives as decimal strings and is formatted as text, never through binary floats.
import { REASON_REGISTRY, type ReasonCode, type Requirement } from './contracts'

const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

function groupThousands(digits: string): string {
  return digits.replace(/\B(?=(\d{3})+(?!\d))/g, ',')
}

/** "1010.00" → "€1,010.00" */
export function formatMoney(value: string): string {
  const [whole, fraction = ''] = value.trim().split('.')
  const negative = whole.startsWith('-')
  const digits = groupThousands(negative ? whole.slice(1) : whole)
  return `${negative ? '−' : ''}€${digits}.${(fraction + '00').slice(0, 2)}`
}

/** "20000" → "20,000"; keeps given decimals ("2.5" → "2.5"). */
export function formatNumber(value: string): string {
  const [whole, fraction] = value.trim().split('.')
  const trimmed = fraction?.replace(/0+$/, '')
  return groupThousands(whole) + (trimmed ? `.${trimmed}` : '')
}

/** ISO date "2026-11-16" → "Mon 16 Nov" */
export function formatDate(value: string): string {
  const [year, month, day] = value.slice(0, 10).split('-').map(Number)
  const weekday = new Date(Date.UTC(year, month - 1, day)).getUTCDay()
  return `${WEEKDAYS[weekday]} ${day} ${MONTHS[month - 1]}`
}

function dayNumber(value: string): number {
  const [year, month, day] = value.slice(0, 10).split('-').map(Number)
  return Date.UTC(year, month - 1, day) / 86_400_000
}

/**
 * T97: delivery before the requested date is a trade-off for a kitchen (storage, freshness), so it is said
 * out loud: "Arrives Mon 16 Nov, 4 days early". "Part arrives" when only some lines come early. Null when
 * nothing arrives before the requested date.
 */
export function earlyDeliveryNote(
  lines: ReadonlyArray<{ deliveryDate: string }>,
  requestedDate: string,
): string | null {
  const requested = dayNumber(requestedDate)
  const early = lines.map((line) => line.deliveryDate).filter((d) => dayNumber(d) < requested)
  if (early.length === 0) return null
  const first = early.sort()[0]
  const days = requested - dayNumber(first)
  const who = early.length === lines.length ? 'Arrives' : 'Part arrives'
  return `${who} ${formatDate(first)}, ${days} ${days === 1 ? 'day' : 'days'} early`
}

const helsinki = new Intl.DateTimeFormat('en-GB', {
  timeZone: 'Europe/Helsinki',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
})

/** Timestamp with offset → "Sun 15 Nov 17:00" in Europe/Helsinki. */
export function formatTime(value: string): string {
  const parts = Object.fromEntries(
    helsinki.formatToParts(new Date(value)).map((part) => [part.type, part.value]),
  )
  return `${formatDate(`${parts.year}-${parts.month}-${parts.day}`)} ${parts.hour}:${parts.minute}`
}

/** Helsinki clock time only: "22:05" */
export function formatClock(value: string): string {
  return formatTime(value).slice(-5)
}

const UNIT_LABELS: Record<string, string> = { l: 'l', kg: 'kg', pcs: 'pcs', cases: 'cases' }

export function formatQuantity(value: string, unit: string): string {
  return `${formatNumber(value)} ${UNIT_LABELS[unit] ?? unit}`
}

// GS1 allergen codes (brief Appendix A1) in plain words.
const ALLERGENS: Record<string, string> = {
  AC: 'crustaceans',
  AE: 'egg',
  AF: 'fish',
  AM: 'milk',
  AN: 'tree nuts',
  AP: 'peanuts',
  AS: 'sesame',
  AU: 'sulphites',
  AW: 'gluten cereals',
  AY: 'soy',
  BC: 'celery',
  BM: 'mustard',
  ML: 'lactose',
  NL: 'lupin',
  UM: 'molluscs',
  UW: 'wheat',
  GO: 'oats',
}

const CLAIMS: Record<string, string> = {
  FREE_FROM_GLUTEN: 'Gluten-free',
  FREE_FROM_LACTOSE: 'Lactose-free',
  VEGAN: 'Vegan',
  VEGETARIAN: 'Vegetarian',
}

export function allergenName(code: string): string {
  return ALLERGENS[code] ?? `allergen ${code}`
}

function titleCase(code: string): string {
  const words = code.toLowerCase().split('_')
  return words.map((w, i) => (i === 0 ? w[0].toUpperCase() + w.slice(1) : w)).join(' ')
}

export function requirementLabel(requirement: Requirement): string {
  if (requirement.kind === 'CLAIM') return CLAIMS[requirement.code] ?? titleCase(requirement.code)
  return `No ${allergenName(requirement.code)}`
}

export function reasonChip(code: ReasonCode): string {
  return REASON_REGISTRY[code].chipText
    .replace('{allergen}', 'allergen')
    .replace('{claim}', 'as required')
}

/** Fill a registry template; unknown placeholders stay visible rather than silently blank. */
export function reasonText(code: ReasonCode, params: Record<string, string>): string {
  return REASON_REGISTRY[code].template.replace(
    /\{(\w+)\}/g,
    (match, key: string) => params[key] ?? match,
  )
}
