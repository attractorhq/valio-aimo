import { expect, test, type Page } from '@playwright/test'

// T109: plain one-line reasons for a split, a quantity above the amount asked, a substitute, an early
// delivery, and a short evidence line per product with the full fact list behind "Show all facts".
const STORE = 'verified-or-ask.mock-gateway.v1'
const step = (page: Page, name: string) => page.getByRole('listitem', { name: new RegExp(name) })

async function runScenarioB(page: Page) {
  await page.goto('/')
  await page.getByRole('button', { name: /^Scenario B:/ }).click()
  await expect(step(page, 'Approval')).toContainText('Waiting for approver')
}

type Line = Record<string, unknown> & { facts: Array<Record<string, unknown>> }

test('T109: the approver sees why the order was split, why the quantity is above the ask, and why Monday', async ({
  page,
}) => {
  await runScenarioB(page)
  await page.evaluate((key) => {
    const store = JSON.parse(localStorage.getItem(key) ?? '{}')
    for (const r of Object.values(store) as Array<{
      proposal: {
        requestedDate: string
        reasons: string[]
        request: { items: unknown[] }
        lines: Line[]
      }
    }>) {
      const base = r.proposal.lines[0]
      const stock = (value: string) => ({
        fact: 'stock.quantity',
        value,
        display: `${value} in stock`,
        level: 'VERIFIED',
        source: {
          system: 'valio-aimo.availability-api',
          field: 'scheduleLines[0].availableAmount',
          retrievedAt: '2026-11-15T03:12:00Z',
        },
        method: null,
        conflict: null,
      })
      r.proposal.requestedDate = '2026-11-19'
      r.proposal.reasons = ['SPLIT', 'OVER_AUTO_LIMIT']
      r.proposal.request.items = [
        { itemId: 'I1', what: 'edam cheese', quantity: '200', unit: 'kg' },
      ]
      const line = (id: string, name: string, units: string, kg: string, facts: unknown[]) => ({
        ...base,
        lineId: id,
        itemId: 'I1',
        productId: `SYN-${id}`,
        name,
        salesUnitLabel: 'pc',
        contentPerSalesUnit: { value: '15.5', unit: 'kg', level: 'DECLARED', method: 'synthetic' },
        quantitySalesUnits: units,
        deliveredQuantity: { value: kg, unit: 'kg' },
        deliveryDate: '2026-11-16',
        facts,
        reasons: [],
      })
      r.proposal.lines = [
        line('L1', 'Synthetic edam A', '8', '124', [stock('8')]),
        line('L2', 'Synthetic edam B', '5', '77.5', [stock('40')]),
      ]
    }
    localStorage.setItem(key, JSON.stringify(store))
  }, STORE)

  await page.goto('/approver')
  await page.getByLabel('Signed in as').selectOption('mock-school')
  const card = page.getByRole('article')
  const why = card.getByRole('region', { name: 'Why you are asked' })
  await expect(why).toContainText('Split because Synthetic edam A has only 8 pc (124 kg) in stock.')
  await expect(why).toContainText(
    'Valio offers delivery on Mon 16 Nov for these products; they arrive 3 days before Thursday.',
  )
  await expect(card.getByTestId('quantity-note').first()).toHaveText(
    '201.5 kg: rounded up to whole packs (8 × 15.5 kg + 5 × 15.5 kg); 200 kg asked',
  )
})

test('T109: each trust card starts with one plain evidence line; the full list is behind "Show all facts"', async ({
  page,
}) => {
  await runScenarioB(page)
  await page.getByRole('button', { name: 'Order details' }).click()
  const card = page.getByRole('article', { name: /Trust card for Synthetic gluten-free macaroni/ })
  const summary = card.getByTestId('evidence-summary')
  await expect(summary).toContainText(/gluten-free/i)
  await expect(card.locator('[data-fact]')).toHaveCount(0)
  await expect(card).not.toContainText(/Conflict · K\d/)
  await card.getByRole('button', { name: /Show all facts/ }).click()
  await expect(card.locator('[data-fact]').first()).toBeVisible()
  await expect(card.getByRole('button', { name: 'Hide facts' })).toBeVisible()
})
