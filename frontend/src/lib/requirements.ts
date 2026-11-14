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
  clause: string
} {
  const unproven = checks.filter((c) => !c.proven)
  if (unproven.length > 0) {
    return {
      allProven: false,
      clause: `Not every hard requirement is proven (${names(unproven)}: see the trust cards)`,
    }
  }
  return {
    allProven: true,
    clause:
      checks.length > 0
        ? `Every hard requirement (${names(checks)}) is proven`
        : 'No hard requirements were stated',
  }
}
