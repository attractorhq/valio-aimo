import type { OfferLine, Proposal, RequestItem, Requirement, TrustLabel } from './contracts'
import { requirementLabel } from './format'

/**
 * One hard requirement on one line, with the evidence the gateway attached to that line (T41).
 *
 * The gateway puts the exact requirement facts its policy decided on (`allergen.<code>`, `claim.<code>`)
 * on every proposal line. The trust card, the decision footer and the log all read them through this
 * module, so the card can never say "Unknown" while the footer says "proven".
 */
export type RequirementCheck = {
  requirement: Requirement
  /** The buyer's mandate forbids this allergen; the request itself did not mention it. */
  fromMandate: boolean
  fact?: TrustLabel
  proven: boolean
}

const PROVEN_ALLERGEN_VALUES = new Set(['FREE_FROM', 'NOT_LISTED'])

export function requirementFactKey(requirement: Requirement): string {
  return requirement.kind === 'CLAIM' ? `claim.${requirement.code}` : `allergen.${requirement.code}`
}

export function isRequirementFact(fact: TrustLabel): boolean {
  return fact.fact.startsWith('allergen.') || fact.fact.startsWith('claim.')
}

export function isProven(requirement: Requirement, fact?: TrustLabel): boolean {
  if (!fact || fact.level === 'UNKNOWN' || fact.conflict) return false
  if (requirement.kind === 'CLAIM') return fact.value === true || fact.value === requirement.code
  return typeof fact.value === 'string' && PROVEN_ALLERGEN_VALUES.has(fact.value)
}

export function requirementChecks(
  line: OfferLine,
  item?: RequestItem,
  forbiddenAllergens: readonly string[] = [],
): RequirementCheck[] {
  const own = item?.requirements ?? []
  const seen = new Set(own.map(requirementFactKey))
  const fromMandate: Requirement[] = forbiddenAllergens
    .map((code): Requirement => ({ kind: 'AVOID_ALLERGEN', code }))
    .filter((req) => !seen.has(requirementFactKey(req)))
  return [
    ...own.map((requirement) => ({ requirement, fromMandate: false })),
    ...fromMandate.map((requirement) => ({ requirement, fromMandate: true })),
  ].map(({ requirement, fromMandate: mandated }) => {
    const fact = line.facts.find((f) => f.fact === requirementFactKey(requirement))
    return { requirement, fromMandate: mandated, fact, proven: isProven(requirement, fact) }
  })
}

/** Every requirement check across the proposal's lines, in line order. */
export function proposalRequirementChecks(
  proposal: Proposal,
  forbiddenAllergens: readonly string[] = [],
): Array<RequirementCheck & { line: OfferLine }> {
  const items = new Map(proposal.request.items.map((item) => [item.itemId, item]))
  return proposal.lines.flatMap((line) =>
    requirementChecks(line, items.get(line.itemId), forbiddenAllergens).map((check) => ({
      ...check,
      line,
    })),
  )
}

function names(checks: readonly RequirementCheck[]): string {
  return [...new Set(checks.map((c) => requirementLabel(c.requirement).toLowerCase()))].join(', ')
}

/**
 * What the decision text may say about the hard requirements. "Proven" only when every requirement
 * check on every line is proven by the evidence the trust cards show.
 */
export function requirementsStatement(checks: readonly RequirementCheck[]): {
  allProven: boolean
  hasRequirements: boolean
  clause: string
} {
  const unproven = checks.filter((c) => !c.proven)
  if (unproven.length > 0) {
    return {
      allProven: false,
      hasRequirements: true,
      clause: `Not every hard requirement is proven (${names(unproven)}: see the trust cards)`,
    }
  }
  // T78: say how the requirements are proven, never more than the evidence shows.
  return {
    allProven: true,
    hasRequirements: checks.length > 0,
    clause:
      checks.length > 0
        ? `Every hard requirement (${names(checks)}) is ${provenBy(checks.map((c) => c.fact))}`
        : 'Every decisive fact on this order is verified or declared',
  }
}

const SUPPLIER_SOURCE = 'valio-aimo.product-data'

function isSupplierDeclared(fact: TrustLabel): boolean {
  return fact.level === 'DECLARED' && fact.source?.system === SUPPLIER_SOURCE
}

/** "verified", "declared by the supplier", "verified or declared by the supplier" or "verified or declared". */
function provenBy(facts: ReadonlyArray<TrustLabel | undefined>): string {
  const present = facts.filter((f): f is TrustLabel => Boolean(f))
  const verified = present.some((f) => f.level === 'VERIFIED')
  const declared = present.filter((f) => f.level === 'DECLARED')
  const bySupplier = declared.length > 0 && declared.every(isSupplierDeclared)
  if (declared.length === 0) return 'verified'
  const who = bySupplier ? 'declared by the supplier' : 'declared'
  return verified ? `verified or ${who}` : who
}

/** The facts that decide AUTO on a line: price, stock and delivery date, plus every hard requirement. */
const DECISIVE_FACTS = new Set(['price.unit', 'stock.quantity', 'delivery.date'])

export type EvidenceCounts = {
  verified: number
  supplierDeclared: number
  /** Declared by Valio Aimo itself, e.g. "Stock not tracked · Valio accepts any quantity". */
  valioDeclared: number
  /** Estimate, Unknown, conflicting or missing evidence. */
  weaker: number
}

/** T78: how strong the decisive evidence of a proposal is, counted once per line and fact. */
export function decisiveEvidence(
  proposal: Proposal,
  forbiddenAllergens: readonly string[] = [],
): EvidenceCounts {
  const facts = new Map<string, TrustLabel | undefined>()
  for (const line of proposal.lines) {
    for (const fact of line.facts) {
      if (DECISIVE_FACTS.has(fact.fact)) facts.set(`${line.lineId}:${fact.fact}`, fact)
    }
  }
  for (const check of proposalRequirementChecks(proposal, forbiddenAllergens)) {
    facts.set(`${check.line.lineId}:${requirementFactKey(check.requirement)}`, check.fact)
  }
  const counts: EvidenceCounts = { verified: 0, supplierDeclared: 0, valioDeclared: 0, weaker: 0 }
  for (const fact of facts.values()) {
    if (!fact || fact.conflict || (fact.level !== 'VERIFIED' && fact.level !== 'DECLARED')) counts.weaker += 1
    else if (fact.level === 'VERIFIED') counts.verified += 1
    else if (isSupplierDeclared(fact)) counts.supplierDeclared += 1
    else counts.valioDeclared += 1
  }
  return counts
}

/** The AUTO headline follows the weakest decisive level: never "proven" for a supplier's claim. */
export function evidenceHeadline(counts: EvidenceCounts): string {
  if (counts.weaker > 0) return 'Not every required fact is verified or declared.'
  if (counts.supplierDeclared === 0 && counts.valioDeclared === 0) return 'Every required fact verified.'
  if (counts.valioDeclared === 0) return 'Every required fact verified or declared by the supplier.'
  return 'Every required fact verified or declared.'
}

/** "5 verified · 2 supplier-declared · 1 declared by Valio" (zero parts left out). */
export function evidenceCountsText(counts: EvidenceCounts): string {
  return [
    counts.verified ? `${counts.verified} verified` : '',
    counts.supplierDeclared ? `${counts.supplierDeclared} supplier-declared` : '',
    counts.valioDeclared ? `${counts.valioDeclared} declared by Valio` : '',
    counts.weaker ? `${counts.weaker} unproven` : '',
  ]
    .filter(Boolean)
    .join(' · ')
}
