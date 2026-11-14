import { expect, test } from '@playwright/test'

test('the open spec is linked from the main view and explains labels, reasons and operations', async ({
  page,
}) => {
  await page.goto('/')
  await page.getByRole('link', { name: /Open spec/ }).click()
  await expect(page).toHaveURL(/\/spec$/)
  await expect(
    page.getByRole('heading', { name: 'Agent Trust Labels v0.1 · open specification' }),
  ).toBeVisible()
  // No horizontal page scroll at any width: wide tables and schemas scroll inside their own box.
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  )
  expect(overflow).toBeLessThanOrEqual(1)

  const levels = page.getByRole('region', { name: 'Trust levels' })
  for (const word of ['Confirmed', 'Supplier says', 'Estimate', 'Unknown', 'Conflict · K1']) {
    await expect(levels).toContainText(word)
  }

  const ask = page.getByRole('table', { name: 'Reason codes: Ask a human' })
  await expect(ask.getByRole('row', { name: /OVER_AUTO_LIMIT/ })).toContainText('Yes')
  const exclude = page.getByRole('table', { name: 'Reason codes: Exclude the product' })
  await expect(exclude.getByRole('row', { name: /CLAIM_MISSING/ })).toContainText('No')
  await expect(page.getByRole('table', { name: 'Reason codes: Block the order' })).toContainText(
    'OVER_HARD_LIMIT',
  )

  const operations = page.getByRole('region', { name: 'Operations' })
  await expect(operations).toContainText('decideApproval')
  await expect(operations).toContainText('POST /v0.1/proposals')

  await expect(page.getByRole('link', { name: 'OpenAPI 3.1 (JSON)' })).toHaveAttribute(
    'href',
    '/openapi.json',
  )
  const schemas = page.getByRole('list', { name: 'JSON Schemas' })
  await expect(schemas.getByText('Proposal', { exact: true })).toBeVisible()
  await expect(schemas.getByText('TrustLabel', { exact: true })).toBeVisible()
  await schemas.getByText('TrustLabel', { exact: true }).click()
  await expect(schemas).toContainText('urn:aimo-connect:v0.1:TrustLabel')
})
