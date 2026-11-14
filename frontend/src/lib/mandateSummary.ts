// T68: the mandate as a plain promise a chef can read in five seconds. Computed from the mandate fields and the
// reason registry (which codes ask a human, which are refused), never hand-written per customer.
import { REASON_REGISTRY, type Mandate, type ReasonCode, type ReasonDefinition } from './contracts'
import { allergenName, formatDate, formatMoney } from './format'

export type MandateSummary = {
  alone: string[]
  needsApprover: string[]
  refused: string[]
  approverRole: string
}

type Phrase = string | null

/** The plain phrase for an approvable (ASK) code under this mandate; null when it does not ask here. */
function askPhrase(code: ReasonCode, m: Mandate): Phrase {
  switch (code) {
    case 'OVER_AUTO_LIMIT':
      return `over ${formatMoney(m.autoApproveLimitEur)}`
    case 'DATE_LATER':
      return 'later delivery'
    case 'PARTIAL_AVAILABILITY':
      return 'partial stock'
    case 'SUPPLIER_STOCK_UNKNOWN':
      return m.autoAcceptSupplierStock ? null : 'supplier-warehouse stock'
    case 'VALIO_SUBSTITUTE':
    case 'AGENT_SUBSTITUTE':
      return 'substitutes'
    case 'LOW_RELIABILITY':
      return `reliability under ${Math.round(Number(m.minReliability) * 100)} %`
    case 'QUANTITY_BASIS_WEAK':
      return 'uncertain pack size'
    default:
      return null
  }
}

/** The plain phrase for a refused (EXCLUDE / BLOCK) code under this mandate; null when it does not apply. */
function refusedPhrase(code: ReasonCode, m: Mandate): Phrase {
  switch (code) {
    case 'OVER_HARD_LIMIT':
      return `over ${formatMoney(m.hardLimitEur)}`
    case 'MANDATE_NOT_VALID':
      if (m.validFrom && m.validTo)
        return `orders outside ${formatDate(m.validFrom)} – ${formatDate(m.validTo)}`
      if (m.validTo) return `orders after ${formatDate(m.validTo)}`
      if (m.validFrom) return `orders before ${formatDate(m.validFrom)}`
      return null
    case 'CATEGORY_NOT_ALLOWED':
      return m.allowedCategories?.length ? 'other categories' : null
    case 'ALLERGEN_UNKNOWN':
    case 'ALLERGEN_IMPLICIT':
      return m.allergenMode === 'STRICT' ? 'allergens not declared' : 'unknown allergens'
    case 'ALLERGEN_CONTAINS':
    case 'ALLERGEN_MAY_CONTAIN':
      return m.forbiddenAllergens?.length
        ? `products with ${m.forbiddenAllergens.map(allergenName).join(', ')}`
        : 'allergens you avoid'
    case 'CUTOFF_PASSED':
      return m.enforceCutoff === false ? null : 'passed cut-off'
    case 'SUBSTITUTE_REJECTED':
      return 'substitutes that break a requirement'
    case 'NOT_AVAILABLE':
    case 'SALES_BLOCKED':
      return 'products that cannot be delivered'
    // Everything else is missing or contradictory evidence.
    default:
      return 'missing evidence'
  }
}

// Read order: money first, then the mandate's own scope, then evidence. Codes not listed follow in registry order.
const FIRST: ReasonCode[] = [
  'OVER_AUTO_LIMIT',
  'OVER_HARD_LIMIT',
  'MANDATE_NOT_VALID',
  'CATEGORY_NOT_ALLOWED',
  'ALLERGEN_UNKNOWN',
  'ALLERGEN_CONTAINS',
  'CUTOFF_PASSED',
]

function collect(effects: ReadonlyArray<ReasonDefinition['effect']>, phrase: (c: ReasonCode) => Phrase) {
  const registry = Object.values(REASON_REGISTRY) as ReasonDefinition[]
  const ordered = [
    ...FIRST.map((code) => REASON_REGISTRY[code] as ReasonDefinition),
    ...registry.filter((reason) => !FIRST.includes(reason.code)),
  ]
  const phrases = ordered
    .filter((reason) => effects.includes(reason.effect))
    .map((reason) => phrase(reason.code))
    .filter((p): p is string => Boolean(p))
  return [...new Set(phrases)]
}

export function mandateSummary(m: Mandate): MandateSummary {
  const what = m.allowedCategories?.length
    ? `in-stock, verified products from categories ${m.allowedCategories.join(', ')}`
    : 'in-stock, verified products'
  const alone = [`${what} up to ${formatMoney(m.autoApproveLimitEur)} per delivery day`]
  if (m.autoAcceptValioSubstitutes) alone.push('Valio Aimo substitutes that meet every requirement')
  if (m.autoAcceptSupplierStock) alone.push('supplier-warehouse stock')
  if (m.validTo) alone.push(`until ${formatDate(m.validTo)}`)
  else if (m.validFrom) alone.push(`from ${formatDate(m.validFrom)}`)

  return {
    alone,
    needsApprover: collect(['ASK'], (code) => askPhrase(code, m)),
    refused: collect(['EXCLUDE', 'BLOCK'], (code) => refusedPhrase(code, m)),
    approverRole: m.approver.role,
  }
}

/** The three lines as plain sentences (also what screen readers and tests read). */
export function mandateSummaryLines(m: Mandate): [string, string][] {
  const s = mandateSummary(m)
  const sentence = (parts: string[]) => {
    const text = parts.join(', ')
    return text.charAt(0).toUpperCase() + text.slice(1)
  }
  return [
    ['Agent may order alone', sentence(s.alone)],
    [`Needs the ${s.approverRole}`, sentence(s.needsApprover)],
    ['Always refused', sentence(s.refused)],
  ]
}
