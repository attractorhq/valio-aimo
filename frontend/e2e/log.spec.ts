import { readFileSync } from 'node:fs'
import { expect, test } from '@playwright/test'

const entries = (page: import('@playwright/test').Page) =>
  page.getByRole('list', { name: 'Decision log' }).locator('> li')

test('scenario A: the chain from request to confirmed (simulated) is readable', async ({
  page,
}) => {
  await page.goto('/log/MOCK-P-A')
  await expect(page.getByText('Synthetic mock data')).toBeVisible()
  await expect(page.getByRole('status', { name: 'Hash-chain verification status' })).toContainText(
    'Hash chain verified ✓',
  )
  await expect(entries(page)).toHaveCount(6)
  await expect(entries(page).first()).toContainText('Request structured')
  await expect(entries(page).first()).toContainText('Agent')
  const decision = entries(page).filter({ hasText: 'Gateway decision' })
  await expect(decision).toContainText('Gateway rule')
  await expect(decision).toContainText('Proposed → Agent decided')
  await expect(entries(page).last()).toContainText('Re-checked live → Confirmed (simulated)')
  const offer = entries(page).filter({ hasText: 'Offer at a fixed price' })
  await expect(offer).toContainText('Verified')
  await expect(offer).toContainText('Declared')
  await expect(offer).toContainText('valio-aimo.price-api')
})

test('scenario B: one reason, the rule and the waiting approver', async ({ page }) => {
  await page.goto('/log/MOCK-P-B')
  const decision = entries(page).filter({ hasText: 'Gateway decision' })
  await expect(decision).toContainText('OVER_AUTO_LIMIT')
  await expect(decision).toContainText('Rule')
  await expect(decision).toContainText('O3')
  await expect(decision).toContainText('Proposed → Waiting for approver')
  await expect(entries(page).filter({ hasText: 'Candidate excluded' })).toContainText(
    'SUBSTITUTE_REJECTED',
  )
  await expect(entries(page).last()).toContainText('Waiting for Head Chef (demo)')
})

test('scenario C: instruction ignored and blocked by mandate are visible', async ({ page }) => {
  await page.goto('/log/MOCK-P-C')
  const ignored = entries(page).filter({ hasText: 'Instruction ignored: request text is data' })
  await expect(ignored).toHaveCount(2)
  await expect(entries(page).last()).toContainText('Proposed → Blocked by mandate')
  await expect(entries(page).last()).toContainText('OVER_HARD_LIMIT')
})

test('a superseded version is called out, with the human approval before it', async ({ page }) => {
  await page.goto('/log/MOCK-P-B-RECHECK')
  await expect(entries(page).filter({ hasText: 'Approved by a human' })).toContainText('Human')
  await expect(entries(page).filter({ hasText: 'Approved by a human' })).toContainText(
    '“OK for Monday.”',
  )
  await expect(entries(page).last()).toContainText(
    'Superseded: this version and any approval of it are void',
  )
})

test('the main view links to the decision log of the shown proposal', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: /^Scenario B:/ }).click()
  await page.getByRole('link', { name: 'Open decision log' }).click()
  await expect(page).toHaveURL(/\/log\/MOCK-P-B(-\w+)?$/)
  await expect(entries(page).first()).toContainText('Request structured')
  await page.getByRole('link', { name: 'Back to the purchasing flow' }).click()
  await expect(page).toHaveURL(/\/$/)
})

test('the approver panel links to the decision log', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: /^Scenario B:/ }).click()
  await expect(page.getByRole('listitem', { name: /Approval/ })).toContainText(
    'Waiting for approver',
  )
  await page.goto('/approver')
  await page.getByRole('link', { name: 'Decision log' }).first().click()
  await expect(page).toHaveURL(/\/log\/MOCK-P-B/)
  await expect(entries(page).filter({ hasText: 'Gateway decision' })).toContainText(
    'OVER_AUTO_LIMIT',
  )
})

test('the log downloads as JSON and CSV with every entry', async ({ page }) => {
  await page.goto('/log/MOCK-P-A')
  await expect(entries(page)).toHaveCount(6)
  const downloads = page.getByLabel('Download decision log')

  const [json] = await Promise.all([
    page.waitForEvent('download'),
    downloads.getByRole('link', { name: 'Download log (JSON)' }).click(),
  ])
  expect(json.suggestedFilename()).toBe('decision-log-proposal-MOCK-P-A.json')
  const trail = JSON.parse(readFileSync((await json.path())!, 'utf-8'))
  expect(trail.entries).toHaveLength(6)

  const [csv] = await Promise.all([
    page.waitForEvent('download'),
    downloads.getByRole('link', { name: 'Download log (CSV)' }).click(),
  ])
  expect(csv.suggestedFilename()).toBe('decision-log-proposal-MOCK-P-A.csv')
  const rows = readFileSync((await csv.path())!, 'utf-8')
    .replace(/^﻿/, '')
    .trim()
    .split('\r\n')
  expect(rows[0]).toBe(
    'timestamp,entryId,customerId,proposalId,offerId,offerVersion,offerHash,mandateVersion,actor,event,fromState,toState,rule,reasons,summary,comment,facts',
  )
  expect(rows).toHaveLength(1 + 6)
})

test('the server re-check result is visible: no change, or the change that superseded it', async ({
  page,
}) => {
  await page.goto('/log/MOCK-P-A')
  await expect(page.getByTestId('recheck-summary')).toContainText('Re-checked: no change')
  await expect(
    entries(page).filter({ hasText: 'Re-checked before confirming' }).getByTestId('recheck-result'),
  ).toContainText('Re-checked: no change')

  await page.goto('/log/MOCK-P-B-RECHECK')
  await expect(page.getByTestId('recheck-summary')).toContainText('Re-check found a change')
  await expect(page.getByTestId('recheck-summary')).toContainText('€22.92 to €22.93')
})
