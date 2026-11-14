import { readFileSync } from 'node:fs'
import { expect, test, type Page } from '@playwright/test'
import type { Mandate } from '../src/lib/contracts'

// T57: no developer eval score in the buyer view, and no stray count in Lookup after a clarification.

const mock = <T>(name: string): T =>
  JSON.parse(readFileSync(new URL(`../src/mocks/${name}`, import.meta.url), 'utf-8')) as T
const mandates = [mock<Mandate>('mandate-m1.json'), mock<Mandate>('mandate-m2.json')]
const step = (page: Page, name: string) => page.getByRole('listitem', { name: new RegExp(name) })

const QUESTION =
  'Which product do you need for the brunch for 200 guests, and how much (litres, kilograms, pieces or cases)?'

/** The live gateway, faked: a request that ends in one clarifying question, as the agent run streams it. */
async function fakeClarificationGateway(page: Page) {
  const json = (body: unknown) => ({
    status: 200,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  await page.route('**/health', (route) =>
    route.fulfill(json({ status: 'ok', ready: true, mode: 'LIVE', version: '0.1' })),
  )
  await page.route('**/v0.1/**', (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === '/v0.1/mandates') return route.fulfill(json(mandates))
    if (path === '/v0.1/agent/run') {
      const events = [
        {
          type: 'step',
          step: 1,
          name: 'REQUEST',
          title: 'Request',
          status: 'blocked',
          summary: QUESTION,
          request: null,
          clarificationQuestion: QUESTION,
          reasons: ['CLARIFICATION_NEEDED'],
          excluded: [],
        },
        {
          type: 'result',
          proposal: null,
          request: null,
          clarificationQuestion: QUESTION,
          reasons: ['CLARIFICATION_NEEDED'],
          excluded: [],
          mode: 'LIVE',
          explanation: { text: QUESTION, sentenceCount: 1, reasons: [], usedLlm: false },
        },
      ]
      return route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'application/x-ndjson' },
        body: events.map((e) => JSON.stringify(e)).join('\n') + '\n',
      })
    }
    return route.fulfill({ status: 404, body: '{}' })
  })
}

test('the buyer view shows no eval score badge', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByText(/Eval \d+\/24/)).toHaveCount(0)
  // The live pre-flight is a presenter command (scripts/preflight), not a buyer-view control.
  await expect(page.getByRole('button', { name: 'Pre-flight', exact: true })).toHaveCount(0)
})

test('after a clarification the Lookup step shows only "Not started", no lone digit', async ({
  page,
}) => {
  await fakeClarificationGateway(page)
  await page.goto('/')
  await page
    .getByLabel('What do you need, and by when?')
    .fill('something for the weekend brunch, enough for about 200 guests')
  await page.getByRole('button', { name: 'Run request' }).click()
  await expect(page.getByRole('region', { name: 'Clarification needed' })).toContainText(QUESTION)
  const requestDetails = page.getByRole('button', { name: 'Request details' })
  await requestDetails.click()
  await expect(page.getByRole('region', { name: 'Clarification needed' })).toBeHidden()
  await requestDetails.click()
  await expect(page.getByRole('region', { name: 'Clarification needed' })).toContainText(QUESTION)

  const lookup = step(page, 'Lookup')
  await expect(lookup).toContainText('Not started')
  // The step body, not the numbered rail: "Not started" and no count after it.
  const text = (await lookup.locator('.step-body').innerText()).replace(/\s+/g, ' ')
  expect(text).not.toMatch(/(^| )\d+( |$)/)
})
