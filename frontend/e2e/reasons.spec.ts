import { readFileSync } from 'node:fs'
import { expect, test } from '@playwright/test'
import type { OfferLine, Proposal } from '../src/lib/contracts'
import {
  approvalReasons,
  plainReason,
  productSummary,
  rowStatement,
  uniqueReasons,
} from '../src/components/approver/explain'

// T45: a reason chip is shown once, and never followed by its own label.

const base = JSON.parse(
  readFileSync(new URL('../src/mocks/scenario-b.json', import.meta.url), 'utf-8'),
) as Proposal

test('duplicate reason codes collapse to one chip', () => {
  expect(uniqueReasons(['AGENT_SUBSTITUTE', 'OVER_AUTO_LIMIT', 'AGENT_SUBSTITUTE'])).toEqual([
    'AGENT_SUBSTITUTE',
    'OVER_AUTO_LIMIT',
  ])
  const doubled: Proposal = { ...base, reasons: ['OVER_AUTO_LIMIT', 'OVER_AUTO_LIMIT'] }
  expect(approvalReasons(doubled)).toEqual(['OVER_AUTO_LIMIT'])
})

test('a substitute reason without details adds no text that repeats the chip', () => {
  const proposal: Proposal = { ...base, reasons: ['AGENT_SUBSTITUTE'] }
  expect(plainReason('AGENT_SUBSTITUTE', proposal)).toBeNull()
  expect(plainReason('VALIO_SUBSTITUTE', proposal)).toBeNull()
})

test('a Valio-listed substitute line is named with what it replaces', () => {
  const lines: OfferLine[] = base.lines.map((line, i) =>
    i === 0 ? { ...line, substitutionSource: 'VALIO', substituteFor: '468465' } : line,
  )
  const proposal: Proposal = { ...base, lines, reasons: ['VALIO_SUBSTITUTE'] }
  expect(plainReason('VALIO_SUBSTITUTE', proposal)).toBe(
    `Listed by Valio Aimo as a substitute: ${base.lines[0].name} for 468465.`,
  )
  expect(rowStatement(proposal)).toBe(plainReason('VALIO_SUBSTITUTE', proposal))
})

test('product names: the first two, then +N', () => {
  const third = { ...base.lines[0], lineId: 'L3', name: 'Synthetic third product' }
  const fourth = { ...base.lines[0], lineId: 'L4', name: 'Synthetic fourth product' }
  expect(productSummary({ ...base, lines: [...base.lines, third, fourth] })).toBe(
    `${base.lines[0].name}, ${base.lines[1].name} +2`,
  )
})
