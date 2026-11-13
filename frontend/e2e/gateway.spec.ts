import { expect, test } from '@playwright/test'

test('buyer request → verified proposal → simulated order → audit trail', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'A smarter way to stock up.' })).toBeVisible()
  await page.screenshot({ path: 'test-results/buyer-desktop.png', fullPage: true })
  await page.getByRole('button', { name: 'Find & verify options' }).click()
  const milk = page.locator('.product-card').filter({ hasText: 'DEMO-MLK-02' })
  await expect(milk).toContainText('139.00')
  await page.screenshot({ path: 'test-results/options-desktop.png', fullPage: true })
  await milk.getByRole('button', { name: 'Create proposal' }).click()
  const proposal = page.locator('.proposal-card')
  await expect(proposal).toContainText('Within authority')
  await proposal.getByRole('button', { name: 'Commit simulated order' }).click()
  await expect(proposal).toContainText('No order was sent to Valio.')
  await proposal.getByRole('link', { name: 'View audit trail' }).click()
  await expect(page.locator('.timeline')).toContainText('ORDER SIMULATED')
  await expect(page.locator('.timeline')).toContainText('POLICY EVALUATED')
})

test('over-limit purchase needs a manager and persists review', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'Over limit', exact: true }).click()
  await page.getByRole('button', { name: 'Find & verify options' }).click()
  await page
    .locator('.product-card')
    .filter({ hasText: 'DEMO-MLK-02' })
    .getByRole('button', { name: 'Create proposal' })
    .click()
  await expect(page.locator('.proposal-card')).toContainText('Needs approval')
  await expect(
    page.locator('.proposal-card').getByRole('button', { name: 'Commit simulated order' }),
  ).toHaveCount(0)
  await page.getByLabel('DEMO IDENTITY').selectOption('demo-manager')
  await page.getByRole('link', { name: 'Approvals', exact: true }).click()
  const pending = page.locator('.approval-card').filter({ hasText: '278.00' }).first()
  await pending.getByLabel('Review note').fill('Extra spend authorized for the event')
  await pending.getByRole('button', { name: 'Approve checked terms' }).click()
  const approved = page.locator('.proposal-card').filter({ hasText: '278.00' }).first()
  await expect(approved).toContainText('Manager approved')
  await approved.getByRole('button', { name: 'Commit simulated order' }).click()
  await expect(approved).toHaveCount(0)
  await page.getByRole('link', { name: 'Audit trail', exact: true }).click()
  await expect(page.locator('.timeline')).toContainText('HUMAN APPROVED')
})

test('AI-key-free guided fallback and mobile layout', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/')
  await page
    .getByRole('textbox', { name: 'Message the purchasing assistant' })
    .fill('Order milk for Monday')
  await page.screenshot({ path: 'test-results/buyer-mobile.png', fullPage: true })
  await page.getByRole('button', { name: 'Send message' }).click()
  await expect(page.locator('.chat-answer')).toContainText('No order has been placed.')
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  )
  await page.getByRole('button', { name: 'Open menu' }).click()
  await page.getByRole('link', { name: 'Buying policies' }).click()
  await expect(page.getByRole('heading', { name: 'Define the boundaries.' })).toBeVisible()
})
