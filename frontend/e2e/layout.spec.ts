import { readFileSync } from 'node:fs'
import { expect, test, type Page } from '@playwright/test'
import type { ExcludedProduct, Mandate, Proposal, TrustLabel } from '../src/lib/contracts'

// T43: lead with the result. A heavy official-example run (many excluded candidates, each with a dozen
// facts) must still show the decision and the total without scrolling, and the page must stay short.
// The `/v0.1` gateway is faked with page.route so the run goes through the live code path.

const mock = <T>(name: string): T =>
  JSON.parse(readFileSync(new URL(`../src/mocks/${name}`, import.meta.url), 'utf-8')) as T

const base = mock<Proposal>('scenario-b.json')
const mandates = [mock<Mandate>('mandate-m1.json'), mock<Mandate>('mandate-m2.json')]
const OFFICIAL_EXAMPLE =
  'We need 90 kg of gluten-free macaroni and 80 litres of gluten-free oat drink by Monday.'
const EXCLUDED_COUNT = 40
const FACTS_PER_EXCLUDED = 12

function syntheticFacts(n: number): TrustLabel[] {
  const template = base.lines[0].facts
  return Array.from({ length: FACTS_PER_EXCLUDED }, (_, i) => ({
    ...template[i % template.length],
    fact: `${template[i % template.length].fact}.syn${n}-${i}`,
  }))
}

const excluded: ExcludedProduct[] = [
  ...base.excluded,
  ...Array.from({ length: EXCLUDED_COUNT }, (_, n) => ({
    productId: `SYN-X${String(n).padStart(3, '0')}`,
    name: `Synthetic excluded candidate ${n}`,
    itemId: n % 2 === 0 ? 'I1' : 'I2',
    reasons: [n % 3 === 0 ? 'ALLERGEN_UNKNOWN' : 'CLAIM_MISSING'],
    facts: syntheticFacts(n),
  })),
] as ExcludedProduct[]

const proposal: Proposal = { ...base, excluded, proposalId: 'PR-LAYOUT-v1' }

function ndjson(): string {
  const request = proposal.request
  const events = [
    {
      type: 'step',
      step: 1,
      name: 'REQUEST',
      title: 'Request',
      status: 'done',
      summary: '2 items · structured',
      request,
      reasons: [],
      excluded: [],
    },
    {
      type: 'step',
      step: 2,
      name: 'LOOKUP',
      title: 'Lookup',
      status: 'done',
      summary: 'Shortlist evaluated',
      request,
      reasons: [],
      excluded,
    },
    {
      type: 'step',
      step: 3,
      name: 'ORDER',
      title: 'Order',
      status: 'done',
      summary: 'Proposal at fixed price',
      request,
      reasons: [],
      excluded,
    },
    {
      type: 'step',
      step: 4,
      name: 'APPROVAL',
      title: 'Approval',
      status: 'waiting',
      summary: 'Waiting for Head Chef',
      request,
      proposal,
      decision: proposal.decision,
      reasons: proposal.reasons,
      excluded,
    },
    {
      type: 'result',
      proposal,
      request,
      decision: proposal.decision,
      reasons: proposal.reasons,
      mode: 'RECORDED',
      explanation: {
        text: 'Waiting for the Head Chef.',
        sentenceCount: 1,
        reasons: [],
        usedLlm: false,
      },
    },
  ]
  return events.map((e) => JSON.stringify(e)).join('\n') + '\n'
}

async function fakeGateway(page: Page) {
  const json = (body: unknown, status = 200) => ({
    status,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  await page.route('**/health', (route) =>
    route.fulfill(json({ status: 'ok', ready: true, mode: 'RECORDED', version: '0.1' })),
  )
  await page.route('**/v0.1/**', (route) => {
    const url = new URL(route.request().url())
    if (url.pathname === '/v0.1/mandates') return route.fulfill(json(mandates))
    if (url.pathname === '/v0.1/agent/run') {
      return route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'application/x-ndjson' },
        body: ndjson(),
      })
    }
    if (url.pathname.startsWith('/v0.1/proposals/')) return route.fulfill(json(proposal))
    return route.fulfill(json({ error: { code: 'NOT_FOUND', message: 'not faked' } }, 404))
  })
}

async function runOfficialExample(page: Page) {
  await fakeGateway(page)
  await page.goto('/')
  await page.getByText(mandates[1].organisationName).click()
  await page.getByLabel('What do you need, and by when?').fill(OFFICIAL_EXAMPLE)
  await page.getByRole('button', { name: 'Run request' }).click()
  await expect(page.getByTestId('result-banner')).toBeVisible()
}

for (const size of [
  { width: 1440, height: 900 },
  { width: 390, height: 844 },
]) {
  test(`decision and total are visible without scrolling at ${size.width}×${size.height}`, async ({
    page,
  }) => {
    await page.setViewportSize(size)
    await runOfficialExample(page)
    const banner = page.getByTestId('result-banner')
    await expect(banner.getByText('ASK', { exact: true })).toBeInViewport()
    await expect(banner.getByText('€506.16')).toBeInViewport()
    expect(await page.evaluate(() => window.scrollY)).toBe(0)
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth),
    ).toBeLessThanOrEqual(0)
  })
}

test('the page stays short with defaults, and expanding still shows every fact', async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await runOfficialExample(page)
  const height = await page.evaluate(() => document.documentElement.scrollHeight)
  expect(height).toBeLessThan(3000)

  // Collapsed: a one-line summary per item, no fact rows.
  const lookup = page.getByRole('region', { name: 'Excluded candidates' })
  await expect(lookup).toContainText(`${excluded.length} excluded`)
  await expect(lookup.locator('[data-fact]')).toHaveCount(0)

  // Expanded: every excluded candidate and every one of its facts.
  await lookup.getByRole('button', { name: /Show excluded candidates/ }).click()
  // Each click renames its button to "Hide evidence", so always take the first remaining one.
  const showEvidence = lookup.getByRole('button', { name: /Show evidence/ })
  while ((await showEvidence.count()) > 0) {
    await showEvidence.first().click()
  }
  const totalFacts = excluded.reduce((sum, e) => sum + (e.facts?.length ?? 0), 0)
  await expect(lookup.locator('[data-fact]')).toHaveCount(totalFacts)
  for (const entry of excluded) {
    await expect(lookup.getByText(entry.name).first()).toBeAttached()
  }
})

test('on a phone the request box comes before the mandate panel', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await fakeGateway(page)
  await page.goto('/')
  const request = page.getByLabel('What do you need, and by when?')
  await expect(request).toBeInViewport()
  const requestTop = (await request.boundingBox())!.y
  const mandateTop = (await page.getByRole('heading', { name: 'Buyer mandate' }).boundingBox())!.y
  expect(requestTop).toBeLessThan(mandateTop)
  // The buyer can still be chosen from the top of the page.
  await expect(page.getByRole('link', { name: /Change buyer/ })).toBeVisible()
})

test('T86 lighter layout: one surface per section, dividers instead of nested cards, and >= 50% fewer boxed elements on / and /valio', async ({
  page,
}) => {
  const countBoxedInMain = () =>
    page.evaluate(() => {
      const main = document.querySelector('main')
      if (!main) return 0
      const isTransparent = (c: string) => !c || c === 'rgba(0, 0, 0, 0)' || c === 'transparent'
      let count = 0
      for (const el of Array.from(main.querySelectorAll('*'))) {
        const rect = el.getBoundingClientRect()
        if (rect.width === 0 || rect.height === 0) continue
        const cs = getComputedStyle(el)
        if (cs.display === 'none' || cs.visibility === 'hidden' || cs.opacity === '0') continue
        const hasFullBorder =
          parseFloat(cs.borderTopWidth) > 0 &&
          cs.borderTopStyle !== 'none' &&
          !isTransparent(cs.borderTopColor) &&
          parseFloat(cs.borderRightWidth) > 0 &&
          cs.borderRightStyle !== 'none' &&
          !isTransparent(cs.borderRightColor) &&
          parseFloat(cs.borderBottomWidth) > 0 &&
          cs.borderBottomStyle !== 'none' &&
          !isTransparent(cs.borderBottomColor) &&
          parseFloat(cs.borderLeftWidth) > 0 &&
          cs.borderLeftStyle !== 'none' &&
          !isTransparent(cs.borderLeftColor)
        const hasBg = !isTransparent(cs.backgroundColor)
        if (hasFullBorder && hasBg) count++
      }
      return count
    })

  await page.setViewportSize({ width: 1440, height: 900 })
  await runOfficialExample(page)
  const mainCount = await countBoxedInMain()
  // Down from 58 before T86 (at least 50% reduction requires <= 29; actual is ~10)
  expect(mainCount).toBeLessThanOrEqual(20)

  await page.goto('/valio')
  await expect(
    page.getByRole('heading', { name: /Agent readiness/i }),
  ).toBeVisible()
  const valioCount = await countBoxedInMain()
  // Down from 37 before T86 (at least 50% reduction requires <= 18; actual is ~12)
  expect(valioCount).toBeLessThanOrEqual(18)

  await page.setViewportSize({ width: 390, height: 844 })
  for (const path of ['/', '/approver', '/valio', '/log/MOCK-P-B']) {
    await page.goto(path)
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - window.innerWidth,
    )
    expect(overflow).toBeLessThanOrEqual(0)
  }
})
