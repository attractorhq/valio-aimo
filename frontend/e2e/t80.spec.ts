import { expect, test, type Page } from '@playwright/test'

// T80/T55: lists stay buyer-scoped; approvers explicitly choose their role before loading a queue.

const STORE = 'verified-or-ask.mock-gateway.v1'
const step = (page: Page, name: string) => page.getByRole('listitem', { name: new RegExp(name) })

async function runScenario(page: Page, letter: 'A' | 'B') {
  await page.goto('/')
  await page.getByRole('button', { name: new RegExp(`^Scenario ${letter}:`) }).click()
  await expect(step(page, 'Approval')).toContainText(letter === 'A' ? 'Confirmed' : 'Waiting for approver')
}

test('/log after Scenario A lists the proposal per buyer, and it opens its log', async ({ page }) => {
  await runScenario(page, 'A')
  await page.goto('/log')
  const list = page.getByRole('region', { name: 'Recent proposals' })
  await expect(list).toBeVisible()
  await expect(list).toContainText('AUTO')
  await expect(list).toContainText('Confirmed')
  await expect(list.getByLabel('Filter proposals by buyer')).toBeVisible()
  await expect(page.getByText('Select a proposal to view its decision log.')).toHaveCount(0)

  await list.getByRole('table').first().getByRole('link').first().click()
  await expect(page).toHaveURL(/\/log\/.+/)
  await expect(page.getByRole('heading', { name: /Decision log/ })).toBeVisible()
  await expect(page.getByRole('region', { name: 'Recent proposals' })).toHaveCount(0)
})

test('an unknown proposal id says not found and still lists the proposals', async ({ page }) => {
  await runScenario(page, 'A')
  await page.goto('/log/PR-DOES-NOT-EXIST')
  await expect(page.getByRole('alert')).toContainText('Proposal not found')
  await expect(page.getByRole('region', { name: 'Recent proposals' })).toBeVisible()
})

test('the Approver panel asks for a role instead of choosing the newest pending buyer', async ({
  page,
}) => {
  await runScenario(page, 'B')
  // A newer hotel proposal waiting for sign-off (the mock server's state, as a hotel ASK on the gateway).
  await page.evaluate((key) => {
    const store = JSON.parse(localStorage.getItem(key) ?? '{}')
    const pending = Object.values(store).find(
      (r) => (r as { proposal: { state: string } }).proposal.state === 'PENDING_APPROVAL',
    ) as { proposal: Record<string, unknown> }
    const copy = JSON.parse(JSON.stringify(pending))
    copy.proposal.proposalId = `${pending.proposal.proposalId}-HOTEL`
    copy.proposal.customerId = 'mock-hotels'
    copy.proposal.createdAt = new Date(Date.now() + 60_000).toISOString()
    store[copy.proposal.proposalId] = copy
    localStorage.setItem(key, JSON.stringify(store))
  }, STORE)

  await page.goto('/approver')
  const signedInAs = page.getByRole('combobox')
  await expect(signedInAs).toHaveValue('')
  await expect(page.locator('.approver-queue button')).toHaveCount(0)
  await signedInAs.selectOption('mock-school')
  await expect(page.getByRole('heading', { level: 1 })).toContainText('Head Chef')
  await expect(page.locator('.approver-queue button')).toHaveCount(1)
  await expect(page.locator('.approver-queue button').first()).toContainText('€506.16')
  await expect(page.locator('.approver-queue')).not.toContainText('HOTEL')
})
