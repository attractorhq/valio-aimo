import { expect, test, type Page } from '@playwright/test'

const STORE = 'verified-or-ask.mock-gateway.v1'
const step = (page: Page, name: string) => page.getByRole('listitem', { name: new RegExp(name) })

async function runScenarioB(page: Page) {
  await page.goto('/')
  await page.getByRole('button', { name: /^Scenario B:/ }).click()
  await expect(step(page, 'Approval')).toContainText('Waiting for approver')
}

/** Edit the mock server's state, as a changed fact or a passing clock would on the real gateway. */
async function patchPending(
  page: Page,
  patch: Record<string, unknown>,
  record: Record<string, unknown> = {},
) {
  await page.evaluate(
    ([key, patch, record]) => {
      const store = JSON.parse(localStorage.getItem(key) ?? '{}')
      for (const r of Object.values(store) as Array<{ proposal: { state: string } }>) {
        if (r.proposal.state === 'PENDING_APPROVAL' || record.force) {
          Object.assign(r.proposal, patch)
          Object.assign(r, record)
        }
      }
      localStorage.setItem(key, JSON.stringify(store))
    },
    [STORE, patch, record] as const,
  )
}

test('B: main view ASK → Approver panel → Approve → confirmed in both views', async ({
  page,
  context,
}, info) => {
  await runScenarioB(page)
  await expect(page.getByRole('button', { name: /approve/i })).toHaveCount(0)

  const approver = await context.newPage()
  await approver.goto('/approver')
  await expect(approver.getByRole('heading', { name: 'Approver — Head Chef (demo)' })).toBeVisible()
  const card = approver.getByRole('article')
  await expect(card.getByRole('heading')).toHaveText('You are approving version 1 of this proposal')
  await expect(card).toContainText(
    '€506.16 is above your €500.00 auto-approve limit. Everything else is verified or declared.',
  )
  await expect(card).toContainText('Synthetic gluten-free macaroni 5 kg')
  await expect(card).toContainText('18 bag')
  await expect(card).toContainText('Mon 16 Nov')
  await expect(card).toContainText('Demo approval link — single use')
  await approver.screenshot({
    path: `test-results/approver-pending-${info.project.name}.png`,
    fullPage: true,
  })

  // The agent view never holds the approval capability.
  const token = await approver.evaluate((key) => {
    const store = JSON.parse(localStorage.getItem(key) ?? '{}')
    return (Object.values(store) as Array<{ approvalToken?: string }>).find((r) => r.approvalToken)
      ?.approvalToken
  }, STORE)
  expect(token).toBeTruthy()
  expect(await page.content()).not.toContain(token!)

  await card.getByRole('button', { name: 'Approve €506.16' }).click()
  await expect(card).toContainText(
    /Approved · re-checked \(recorded data\) · Confirmed \(simulated\) · logged \d{2}:\d{2}/,
  )
  await expect(card.getByRole('button', { name: /Approve/ })).toHaveCount(0)
  await approver.screenshot({
    path: `test-results/approver-approved-${info.project.name}.png`,
    fullPage: true,
  })

  await page.bringToFront()
  await expect(step(page, 'Approval')).toContainText(
    'Approved by Head Chef (demo) → Confirmed (simulated)',
  )
  await expect(step(page, 'Approval')).toContainText('Approved')
  await page.screenshot({
    path: `test-results/main-after-approval-${info.project.name}.png`,
    fullPage: true,
  })
})

test('reject is shown in both views and nothing is ordered', async ({ page, context }) => {
  await runScenarioB(page)
  const approver = await context.newPage()
  await approver.goto('/approver')
  await approver.getByLabel('Comment (optional)').fill('Too expensive this week.')
  await approver.getByRole('button', { name: 'Reject' }).click()
  await expect(approver.getByRole('article')).toContainText('Rejected · nothing was ordered')
  await page.bringToFront()
  await expect(step(page, 'Approval')).toContainText('Rejected by Head Chef (demo)')
})

test('a used approval link cannot be replayed', async ({ page }) => {
  await runScenarioB(page)
  await page.goto('/approver')
  await page.getByRole('button', { name: 'Approve €506.16' }).click()
  await expect(page.getByRole('article')).toContainText('Confirmed (simulated)')
  // Replay: the same token is presented again for a proposal that looks pending.
  await patchPending(page, { state: 'PENDING_APPROVAL' }, { force: true })
  await page.reload()
  await page.getByRole('button', { name: 'Approve €506.16' }).click()
  await expect(page.getByRole('alert')).toContainText('This approval link was already used')
})

test('an expired offer can no longer be approved', async ({ page }) => {
  await runScenarioB(page)
  await patchPending(page, { validUntil: '2026-01-01T00:00:00+02:00' })
  await page.goto('/approver')
  const card = page.getByRole('article')
  await expect(card).toContainText('Expired · the offer ran out before sign-off')
  await expect(card.getByRole('button', { name: /Approve/ })).toHaveCount(0)
})

test('a superseded version can no longer be approved', async ({ page }) => {
  await runScenarioB(page)
  await patchPending(page, { state: 'SUPERSEDED', supersededBy: 'MOCK-P-B-v2' })
  await page.goto('/approver')
  const card = page.getByRole('article')
  await expect(card).toContainText('Superseded · something changed at the re-check')
  await expect(card).toContainText('MOCK-P-B-v2')
  await expect(card.getByRole('button', { name: /Approve/ })).toHaveCount(0)
})

test('the approver panel is visibly not the agent and starts empty', async ({ page }) => {
  await page.goto('/approver')
  await expect(page.getByText('Human sign-off channel · not the agent')).toBeVisible()
  await expect(page.getByText('Nothing to approve.')).toBeVisible()
  await page.getByLabel('Signed in as').selectOption('mock-hotels')
  await expect(page.getByRole('heading', { level: 1 })).toHaveText(
    'Approver — Purchasing Manager (demo)',
  )
})

test('the approver panel has no horizontal page scroll', async ({ page }) => {
  await runScenarioB(page)
  await page.goto('/approver')
  await expect(page.getByRole('button', { name: 'Approve €506.16' })).toBeVisible()
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth,
  )
  expect(overflow).toBeLessThanOrEqual(0)
})

test('phone approval via QR (/approve/:token) at 390 px and across separate device contexts', async ({
  page,
  browser,
}) => {
  await runScenarioB(page)
  // Agent view never shows a QR code or approval link (P3).
  await expect(page.getByRole('img', { name: /Scan on a phone/i })).toHaveCount(0)

  await page.goto('/approver')
  const qr = page.getByRole('img', {
    name: 'Scan on a phone to open the single-use demo approval page',
  })
  await expect(qr).toBeVisible()
  const mobileHref = await page
    .getByRole('link', { name: 'Open mobile approval page' })
    .getAttribute('href')
  expect(mobileHref).toMatch(/^\/approve\/tk-B-/)

  // Open on a separate browser context (simulating a judge's phone on mobile data, 390 px wide).
  const phoneContext = await browser.newContext({ viewport: { width: 390, height: 844 } })
  const phone = await phoneContext.newPage()
  await phone.goto(mobileHref!)

  await expect(phone.getByRole('heading', { name: 'Approver — Head Chef (demo)' })).toBeVisible()
  const card = phone.getByRole('article')
  await expect(card.getByRole('heading')).toHaveText('You are approving version 1 of this proposal')
  await expect(card).toContainText(
    '€506.16 is above your €500.00 auto-approve limit. Everything else is verified or declared.',
  )
  await expect(card).toContainText('Synthetic gluten-free macaroni 5 kg')
  await expect(card).toContainText('Demo approval link — single use')

  const overflow = await phone.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth,
  )
  expect(overflow).toBeLessThanOrEqual(0)

  await card.getByRole('button', { name: 'Approve €506.16' }).click()
  await expect(card).toContainText(
    /Approved · re-checked \(recorded data\) · Confirmed \(simulated\) · logged \d{2}:\d{2}/,
  )
  await phoneContext.close()
})
