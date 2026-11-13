import { expect, test } from '@playwright/test'

// Opt-in: uses the actual authenticated challenge APIs and an isolated imported database.
// External dates, stock and prices can change; this is intentionally not a hermetic CI test.
test('supplier catalog → live price/stock → authorization → simulated commitment', async ({
  page,
}) => {
  await page.goto('/')
  await expect(page.getByText('LIVE CHALLENGE API', { exact: true })).toBeVisible()
  await page.getByPlaceholder('Product name or code').fill('463335')
  await page.getByRole('button', { name: 'Find & verify options' }).click()
  const milk = page.locator('.product-card').filter({ hasText: '463335' })
  await expect(milk).toContainText('Valio Eila')
  await expect(milk).toContainText('Available for')
  await milk.getByRole('button', { name: 'Create proposal', exact: true }).click()
  const proposal = page.locator('.proposal-card')
  await expect(proposal).toContainText('Within authority')
  await proposal.getByRole('button', { name: 'Commit simulated order' }).click()
  await expect(proposal).toContainText('No order was sent to Valio.')
  await proposal.getByRole('link', { name: 'View audit trail' }).click()
  await expect(page.locator('.timeline')).toContainText('Valio Junction API /api/price')
  await expect(page.locator('.timeline')).toContainText('ORDER SIMULATED')
  await page.screenshot({ path: 'test-results/live-api-audit.png', fullPage: true })
})
