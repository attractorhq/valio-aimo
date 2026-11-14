import { readFileSync } from 'node:fs'
import { expect, test, type Page } from '@playwright/test'
import type { Proposal } from '../src/lib/contracts'
import { explainWhyNotAuto, plainReason } from '../src/components/approver/explain'
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
  expect(statement.hasRequirements).toBe(true)
  await expect(page.getByTestId('decision-summary')).toContainText(statement.clause)
  await expect(page.getByTestId('decision-summary')).toContainText(
    'Every hard requirement (gluten-free) is declared by the supplier',
  )
  // The 'why' is said once, in the result card (T83: each thing once per view).
  await expect(page.getByTestId('result-why')).toContainText('auto-approve limit')
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
test('T86: empty requirement list never says "No hard requirements were stated" and plainReason fills all ASK/INFO templates', () => {
  const emptyStatement = requirementsStatement([])
  expect(emptyStatement.allProven).toBe(true)
  expect(emptyStatement.hasRequirements).toBe(false)
  expect(emptyStatement.clause).not.toContain('No hard requirements were stated')

  const multiAskProposal: Proposal = {
    ...proposalB,
    requestedDate: '2026-11-13',
    reasons: [
      'DATE_LATER',
      'PARTIAL_AVAILABILITY',
      'LOW_RELIABILITY',
      'SUPPLIER_STOCK_UNKNOWN',
      'QUANTITY_BASIS_WEAK',
      'OVER_AUTO_LIMIT',
    ],
    request: {
      ...proposalB.request,
      requestedDate: '2026-11-13',
      items: [
        {
          ...proposalB.request.items[0],
          quantity: '120',
          requirements: [],
        },
        {
          ...proposalB.request.items[1],
          requirements: [],
        },
      ],
    },
    lines: proposalB.lines.map((line, idx) =>
      idx === 0
        ? {
            ...line,
            deliveredQuantity: { value: '95', unit: 'kg' },
            facts: [
              ...line.facts.filter((f) => f.fact !== 'reliability'),
              {
                fact: 'reliability',
                value: 0.91,
                display: 'Delivered in full 91.0% historically',
                level: 'ESTIMATE',
                source: {
                  system: 'valio-aimo.delivery-reliability',
                  field: 'deliveryReliability',
                  retrievedAt: null,
                },
              },
            ],
          }
        : line,
    ),
  }

  expect(plainReason('DATE_LATER', multiAskProposal)).toBe(
    'Earliest delivery Mon 16 Nov. You asked for Fri 13 Nov.',
  )
  expect(plainReason('PARTIAL_AVAILABILITY', multiAskProposal)).toBe(
    'Only 95 kg of macaroni in stock; 120 kg needed.',
  )
  expect(plainReason('LOW_RELIABILITY', multiAskProposal)).toBe(
    'Delivered in full only 91 % of the time historically.',
  )
  expect(plainReason('SUPPLIER_STOCK_UNKNOWN', multiAskProposal)).toBe(
    "Ships from the supplier's warehouse. Valio Aimo can't see the quantity.",
  )
  expect(plainReason('QUANTITY_BASIS_WEAK', multiAskProposal)).toBe(
    'Pack size read from the product name only.',
  )
  expect(plainReason('REQUOTED', multiAskProposal)).toBe(
    'Re-checked live against current price, stock, cut-off and mandate before ordering.',
  )

  const combined = explainWhyNotAuto(multiAskProposal)
  expect(combined).toContain('earliest delivery is Mon 16 Nov instead of requested Fri 13 Nov')
  expect(combined).toContain('only 95 kg of the requested 120 kg of macaroni is available in stock')
  expect(combined).toContain('historical delivery reliability (91 %) is below the mandate threshold')
  expect(combined).toContain('the order total (€506.16 excl. VAT) is above your €500.00 auto-approve limit')
})
