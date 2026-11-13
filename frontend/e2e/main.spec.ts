import { expect, test, type Page } from '@playwright/test'

const step = (page: Page, name: string) => page.getByRole('listitem', { name: new RegExp(name) })

async function runScenario(page: Page, id: 'A' | 'B' | 'C') {
  await page.getByRole('button', { name: new RegExp(`^Scenario ${id}:`) }).click()
  await expect(step(page, 'Approval')).toHaveAttribute('aria-current', 'step')
}

test.beforeEach(async ({ page }) => {
  await page.goto('/')
  await expect(page.getByText('Synthetic mock data')).toBeVisible()
})

test('shows one main view with four steps and a read-only mandate', async ({ page }) => {
  for (const name of ['Request', 'Lookup', 'Order', 'Approval']) {
    await expect(step(page, name)).toContainText('Not started')
  }
  const mandate = page.getByRole('region', { name: 'Buyer mandate' })
  await expect(mandate).toContainText('Read-only')
  await expect(mandate).toContainText('€1,500.00 excl. VAT')
  await mandate.getByText('Synthetic School Kitchen (demo mock)').click()
  await expect(mandate).toContainText('€500.00 excl. VAT')
  await expect(mandate).toContainText('€3,000.00 excl. VAT')
  await expect(mandate).toContainText('Strict')
  await expect(mandate).toContainText('Head Chef (demo)')
})

test('scenario A: agent decided → confirmed (simulated)', async ({ page }, info) => {
  await runScenario(page, 'A')
  await expect(step(page, 'Request')).toContainText('Lactose-free')
  await expect(step(page, 'Request')).toContainText('Mon 16 Nov')
  await expect(step(page, 'Order')).toContainText(
    'Proposal at fixed price · offer v1 · €1,010.00 excl. VAT',
  )
  const approval = step(page, 'Approval')
  await expect(approval).toContainText('Agent decided → Confirmed (simulated)')
  await expect(approval).toContainText('Simulated order — mock environment')
  await expect(page.getByText(/Recorded · captured Fri 13 Nov 21:50/)).toBeVisible()
  await page.screenshot({
    path: `test-results/scenario-a-${info.project.name}.png`,
    fullPage: true,
  })
})

test('scenario B: waiting for approver, exactly one reason, no approve button', async ({
  page,
}, info) => {
  await runScenario(page, 'B')
  await expect(page.getByRole('radio', { name: /School Kitchen/ })).toBeChecked()
  await expect(step(page, 'Order')).toContainText('offer v1 · €506.16 excl. VAT')
  const approval = step(page, 'Approval')
  await expect(approval).toContainText('Waiting for approver')
  await expect(approval).toContainText('Waiting for Head Chef (demo)')
  await expect(approval.getByRole('list', { name: 'Reasons' }).getByRole('listitem')).toHaveCount(1)
  await expect(approval).toContainText(
    '€506.16 excl. VAT is above your €500.00 auto-approve limit.',
  )
  await expect(page.getByRole('button', { name: /approve/i })).toHaveCount(0)
  await expect(step(page, 'Lookup')).toContainText('excluded')
  await page.screenshot({
    path: `test-results/scenario-b-${info.project.name}.png`,
    fullPage: true,
  })
})

test('scenario C: blocked by mandate, instruction ignored', async ({ page }, info) => {
  await runScenario(page, 'C')
  const approval = step(page, 'Approval')
  await expect(approval).toContainText('Blocked by mandate')
  await expect(approval).toContainText('Above hard limit')
  await expect(approval).toContainText('€20,200.00 excl. VAT is above your €5,000.00 hard limit.')
  await expect(approval).toContainText('Instruction ignored')
  await page.screenshot({
    path: `test-results/scenario-c-${info.project.name}.png`,
    fullPage: true,
  })
})

test('free text in mock mode says so instead of inventing a result', async ({ page }) => {
  await page.getByLabel('What do you need, and by when?').fill('5 kg of butter by Friday')
  await page.getByRole('button', { name: 'Run request' }).click()
  await expect(page.getByRole('alert')).toContainText('Free-text requests need the live gateway')
  await expect(step(page, 'Approval')).toContainText('Not started')
})

test('has no horizontal page scroll', async ({ page }) => {
  await runScenario(page, 'B')
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth,
  )
  expect(overflow).toBeLessThanOrEqual(0)
})
