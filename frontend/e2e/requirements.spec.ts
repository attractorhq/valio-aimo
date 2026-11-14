import { readFileSync } from 'node:fs'
import { expect, test, type Page } from '@playwright/test'
import type { Proposal } from '../src/lib/contracts'
import {
  decisiveEvidence,
  evidenceCountsText,
  evidenceHeadline,
  proposalRequirementChecks,
  requirementsStatement,
} from '../src/lib/requirements'

// T41: the trust card's requirement rows and the decision footer's "proven" statement come from the
// same requirement facts on the proposal lines, so they can never contradict each other.

const proposalB = JSON.parse(
  readFileSync(new URL('../src/mocks/scenario-b.json', import.meta.url), 'utf-8'),
) as Proposal
const step = (page: Page, name: string) => page.getByRole('listitem', { name: new RegExp(name) })

function withFact(proposal: Proposal, fact: string, update: Record<string, unknown>): Proposal {
  return {
    ...proposal,
    lines: proposal.lines.map((line, index) =>
      index === 0
        ? { ...line, facts: line.facts.map((f) => (f.fact === fact ? { ...f, ...update } : f)) }
        : line,
    ),
  }
}

test('the card rows and the footer statement read the same requirement facts', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: /^Scenario B:/ }).click()
  await expect(step(page, 'Approval')).toHaveAttribute('aria-current', 'step')

  const checks = proposalRequirementChecks(proposalB)
  expect(checks.length).toBeGreaterThan(0)
  const rows = page.locator('[aria-label^="Trust card for"] [data-requirement]')
  await expect(rows).toHaveCount(checks.length)
  for (const check of checks) {
    const card = page.getByRole('article', { name: `Trust card for ${check.line.name}` })
    await expect(
      card.locator(`[data-requirement="${check.requirement.kind}:${check.requirement.code}"]`),
    ).toHaveAttribute('data-proven', String(check.proven))
  }

  const statement = requirementsStatement(checks)
  expect(statement.allProven).toBe(true)
  await expect(page.getByTestId('decision-summary')).toContainText(statement.clause)
  await expect(page.getByTestId('decision-summary')).toContainText(
    'Every hard requirement (gluten-free) is declared by the supplier',
  )
})

test('an Unknown requirement fact can never be called proven', () => {
  const unknown = withFact(proposalB, 'claim.FREE_FROM_GLUTEN', {
    level: 'UNKNOWN',
    value: null,
    display: 'Unknown',
  })
  const checks = proposalRequirementChecks(unknown)
  expect(checks.some((c) => !c.proven)).toBe(true)
  const statement = requirementsStatement(checks)
  expect(statement.allProven).toBe(false)
  expect(statement.clause).toBe(
    'Not every hard requirement is proven (gluten-free: see the trust cards)',
  )
})

test('a conflicting requirement fact is not proven either', () => {
  const conflicting = withFact(proposalB, 'claim.FREE_FROM_GLUTEN', {
    conflict: { code: 'K1', message: 'Gluten-free claim but wheat declared' },
  })
  expect(requirementsStatement(proposalRequirementChecks(conflicting)).allProven).toBe(false)
})

test('a mandate-forbidden allergen without evidence on the line is not proven', () => {
  const checks = proposalRequirementChecks(proposalB, ['BM'])
  const mandated = checks.filter((c) => c.fromMandate)
  expect(mandated).toHaveLength(proposalB.lines.length)
  expect(mandated.every((c) => !c.proven)).toBe(true)
  expect(requirementsStatement(checks).allProven).toBe(false)
})

test('declared free-from allergen evidence is proven', () => {
  const freeFrom = {
    fact: 'allergen.AE',
    value: 'FREE_FROM',
    display: 'Free from egg',
    level: 'DECLARED' as const,
    source: { system: 'valio-aimo.product-data' as const, field: 'nonAllergen', retrievedAt: null },
    method: null,
    conflict: null,
  }
  const withEgg: Proposal = {
    ...proposalB,
    lines: proposalB.lines.map((line) => ({ ...line, facts: [freeFrom, ...line.facts] })),
  }
  const checks = proposalRequirementChecks(withEgg, ['AE'])
  expect(checks.filter((c) => c.fromMandate).every((c) => c.proven)).toBe(true)
  expect(requirementsStatement(checks).clause).toBe(
    'Every hard requirement (gluten-free, no egg) is declared by the supplier',
  )
})

// T78: the AUTO headline follows the weakest decisive level; a supplier's claim is never "proven".
const proposalA = JSON.parse(
  readFileSync(new URL('../src/mocks/scenario-a.json', import.meta.url), 'utf-8'),
) as Proposal

test('the AUTO banner names supplier-declared evidence and counts it', async ({ page }) => {
  const counts = decisiveEvidence(proposalA)
  expect(counts.supplierDeclared).toBeGreaterThan(0)
  expect(evidenceHeadline(counts)).toBe('Every required fact verified or declared by the supplier.')

  await page.goto('/')
  await page.getByRole('button', { name: /^Scenario A:/ }).click()
  const approval = step(page, 'Approval')
  await expect(approval).toContainText('Every required fact verified or declared by the supplier.')
  await expect(approval.getByTestId('evidence-counts')).toHaveText(evidenceCountsText(counts))
  await expect(approval.getByTestId('evidence-counts')).toContainText('supplier-declared')
  await expect(page.getByText('Every fact is proven')).toHaveCount(0)
})

test('all-verified evidence says verified; Valio-declared stock says declared', () => {
  const verifiedOnly = {
    ...proposalA,
    lines: proposalA.lines.map((line) => ({
      ...line,
      facts: line.facts.map((f) => ({ ...f, level: 'VERIFIED' as const })),
    })),
  }
  expect(evidenceHeadline(decisiveEvidence(verifiedOnly))).toBe('Every required fact verified.')
  const sentinel = withFact(proposalA, 'stock.quantity', {
    level: 'DECLARED',
    display: 'Stock not tracked · Valio accepts any quantity',
  })
  const counts = decisiveEvidence(sentinel)
  expect(counts.valioDeclared).toBe(1)
  expect(evidenceHeadline(counts)).toBe('Every required fact verified or declared.')
  expect(evidenceCountsText(counts)).toContain('1 declared by Valio')
})
