import { expect, test } from '@playwright/test'

const scenarios = [
  ['A', 'Ordered', '€1,010.00'],
  ['B', 'Needs Head Chef’s OK', '€506.16'],
  ['C', 'Order blocked', '€20,200.00'],
] as const

for (const [scenario, decision, total] of scenarios) {
  test(`Scenario ${scenario} gives the chef the decision before opening evidence`, async ({
    page,
  }) => {
    await page.setViewportSize({ width: 390, height: 844 })
    await page.goto('/')
    await page.getByRole('button', { name: new RegExp(`^Scenario ${scenario}:`) }).click()
    const result = page.getByTestId('result-banner')
    await expect(result).toBeVisible()
    await page.evaluate(() => window.scrollTo(0, 0))
    const summary = result.getByTestId('result-summary')
    await expect(summary).toContainText(decision)
    await expect(summary).toContainText(total)
    await expect(summary).toBeInViewport({ ratio: 1 })
    const reasons = result.getByRole('list', { name: 'Decision reasons' })
    expect(await reasons.getByRole('listitem').count()).toBeLessThanOrEqual(4)
    await expect(reasons).toBeInViewport({ ratio: 1 })
    for (const step of ['Request', 'Lookup', 'Order', 'Approval']) {
      await expect(page.getByRole('button', { name: `${step} details` })).toHaveAttribute(
        'aria-expanded',
        'false',
      )
    }
    await expect(result).not.toContainText(/OVER_AUTO_LIMIT|OVER_HARD_LIMIT|INSTRUCTION_IGNORED/)
  })
}

test('one disclosure opens the facts and keyboard or touch explains the chips', async ({
  page,
}, testInfo) => {
  await page.goto('/')
  await page.getByRole('button', { name: /^Scenario B:/ }).click()
  await page.getByRole('button', { name: 'Show evidence', exact: true }).click()
  const order = page.getByRole('listitem', { name: /Step 3: Order/ })
  await expect(order.locator('[data-fact="claim.FREE_FROM_GLUTEN"]').first()).toBeVisible()
  await expect(order.getByText('valio-aimo.price-api · catalogPrice.value').first()).toBeVisible()
  await expect(order.getByText('Fri 13 Nov 21:50').first()).toBeVisible()
  await expect(
    page
      .getByRole('listitem', { name: /Step 4: Approval/ })
      .locator('[data-reason-code="OVER_AUTO_LIMIT"]'),
  ).toBeVisible()
  const chip = page.getByRole('button', { name: 'Above auto-approve limit', exact: true })
  if (testInfo.project.use.hasTouch) {
    await chip.tap()
  } else {
    await chip.focus()
  }
  const tooltip = page.getByRole('tooltip').filter({ hasText: 'OVER_AUTO_LIMIT' })
  await expect(tooltip).toBeVisible()
  await chip.press('Escape')
  await expect(tooltip).toBeHidden()
  const supplier = order.getByRole('button', { name: 'Supplier says', exact: true }).first()
  await supplier.focus()
  await expect(
    page.getByRole('tooltip').filter({ hasText: 'Stated by the supplier' }),
  ).toBeVisible()
})
