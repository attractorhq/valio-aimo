import { readFileSync } from 'node:fs'
import { expect, test } from '@playwright/test'
import type { Mandate, Proposal } from '../src/lib/contracts'

const mock = <T>(name: string): T =>
  JSON.parse(readFileSync(new URL(`../src/mocks/${name}`, import.meta.url), 'utf-8')) as T
const mandate = mock<Mandate>('mandate-m1.json')
const scenario = mock<Proposal>('scenario-b.json')

test('shows the search and retries with the original request and the suggested date', async ({ page }) => {
  const requestText = 'I need 10 litres of lactose-free milk today.'
  const lookupDate = '2026-11-16'
  const inputs: { requestText: string; lookupDate?: string }[] = []
  await page.route('**/health', (route) => route.fulfill({ json: { status: 'ok', ready: true, mode: 'RECORDED' } }))
  await page.route('**/v0.1/mandates', (route) => route.fulfill({ json: [mandate] }))
  const proposal = {
    ...scenario,
    customerId: mandate.customerId,
    requestedDate: '2026-11-13',
    reasons: ['DATE_LATER'],
  }
  await page.route('**/v0.1/proposals/*', (route) => route.fulfill({ json: proposal }))
  await page.route('**/v0.1/agent/run', (route) => {
    const input = route.request().postDataJSON()
    inputs.push(input)
    if (input.lookupDate) return route.fulfill({ json: { steps: [], proposal } })
    return route.fulfill({
      json: {
        steps: [{
          step: 4,
          name: 'APPROVAL',
          title: 'Approval',
          status: 'blocked',
          summary: 'The full request could not be fulfilled',
          noOrder: {
            earliestDelivery: lookupDate,
            items: [{
              itemId: 'I1',
              searched: 'milk · lactose-free',
              why: 'Available Mon 16 Nov, after your Fri 13 Nov deadline.',
              closestMatches: [],
            }],
          },
        }],
      },
    })
  })

  await page.goto('/')
  await page.getByLabel('What do you need, and by when?').fill(requestText)
  await page.getByRole('button', { name: 'Run request' }).click()
  const result = page.getByTestId('result-banner')
  await expect(result).toContainText('Searched: milk · lactose-free')
  await expect(result).toContainText('after your Fri 13 Nov deadline')
  await expect(result).toContainText('Earliest delivery: Mon 16 Nov')
  await result.getByRole('button', { name: 'Use Mon 16 Nov' }).click()
  await expect(result).toContainText(/ASK/)
  await expect(page.getByRole('listitem', { name: /Step 3: Order/ }))
    .toContainText('After your Fri 13 Nov deadline')
  expect(inputs).toEqual([
    { customerId: mandate.customerId, requestText },
    { customerId: mandate.customerId, requestText, lookupDate },
  ])
})
