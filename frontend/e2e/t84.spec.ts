import { readFileSync } from 'node:fs'
import { expect, test, type Page } from '@playwright/test'
import type { AuditTrail, Mandate, Proposal } from '../src/lib/contracts'

const mock = <T>(name: string): T =>
  JSON.parse(readFileSync(new URL(`../src/mocks/${name}`, import.meta.url), 'utf-8')) as T
const mandates = [mock<Mandate>('mandate-m1.json'), mock<Mandate>('mandate-m2.json')]
const proposal = { ...mock<Proposal>('scenario-b.json'), proposalId: 'synthetic-loading-b' }
const audit = mock<AuditTrail>('audit-b.json')
const json = (body: unknown) => ({
  status: 200,
  contentType: 'application/json',
  body: JSON.stringify(body),
})
const delay = () => new Promise((resolve) => setTimeout(resolve, 1500))

async function syntheticGateway(
  page: Page,
  options: { slow?: boolean; queue?: boolean; error?: boolean } = {},
) {
  // Loading/layout tests start after an explicit demo sign-in, not at the role/key picker.
  await page.addInitScript(() => {
    sessionStorage.setItem('verified-or-ask.demo-approver-role', 'Head Chef')
    sessionStorage.setItem('verified-or-ask.demo-admin-key', 'demo-admin')
  })
  await page.route('**/health', (route) =>
    route.fulfill(json({ ready: true, status: 'ok', mode: 'RECORDED' })),
  )
  await page.route('**/v0.1/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (options.slow || path.endsWith('/decision')) await delay()
    if (options.error && path === '/v0.1/mandates')
      return route.fulfill({
        status: 403,
        contentType: 'application/json',
        body: '{"detail":"Forbidden"}',
      })
    if (path === '/v0.1/mandates') return route.fulfill(json(mandates))
    if (path.endsWith('/default')) {
      const mandate = mandates.find((m) => path.includes(m.customerId)) ?? mandates[0]
      return route.fulfill(
        json({ mandate, default: mandate, changedFromDefault: false, differences: [] }),
      )
    }
    if (path === '/v0.1/approver/proposals')
      return route.fulfill(json(options.queue ? [proposal] : []))
    if (path === `/v0.1/approver/proposals/${proposal.proposalId}`)
      return route.fulfill(
        json({
          proposal,
          approver: mandates[1].approver,
          approvalToken: 'synthetic-loading-token',
          permissionNotice: 'Demo approval link — single use',
          expiresAt: '2026-11-14T18:00:00+02:00',
        }),
      )
    if (path.endsWith('/decision'))
      return route.fulfill(json({ ...proposal, state: 'CONFIRMED_SIMULATED' }))
    if (path === '/v0.1/audit') return route.fulfill(json(audit))
    if (path === `/v0.1/proposals/${proposal.proposalId}`) return route.fulfill(json(proposal))
    if (path === '/v0.1/audit/verify') return route.fulfill(json({
      verified: true, headHash: '1'.repeat(64), chainLength: audit.entries.length, links: [],
    }))
    return route.fulfill({ status: 404, body: '{}' })
  })
}

for (const view of [
  {
    path: '/',
    loading: 'buyer mandate',
    absent: 'Unchanged from default mandate.',
    ready: 'Buyer mandate',
  },
  {
    path: '/valio',
    loading: 'readiness insights',
    absent: '0 proposals',
    ready: 'Catalogue',
  },
  {
    path: '/approver',
    loading: 'approver queue',
    absent: 'Nothing to approve.',
    ready: 'Nothing to approve.',
  },
  {
    path: '/admin',
    loading: 'admin controls',
    absent: 'Apply policy change',
    ready: 'Apply policy change',
  },
  {
    path: '/log/synthetic-loading-b',
    loading: 'decision log',
    absent: 'Hash chain verified',
    ready: 'Hash chain verified',
  },
]) {
  test(`loading ${view.path}: skeletons, not empty states, on a 1.5s network`, async ({ page }) => {
    await syntheticGateway(page, { slow: true })
    await page.goto(view.path)
    await expect(page.getByLabel(`Loading ${view.loading}`, { exact: true })).toBeVisible()
    await expect(page.getByText(view.absent, { exact: false })).not.toBeVisible()
    await expect(page.locator('.skeleton').first()).toBeVisible()
    await expect(page.getByLabel(`Loading ${view.loading}`, { exact: true })).toHaveCount(0, {
      timeout: 12000,
    })
    await expect(page.getByText(view.ready, { exact: false }).first()).toBeVisible()
  })
}

for (const { path, queue } of [
  { path: '/' },
  { path: '/valio' },
  { path: '/approver' },
  { path: '/approver', queue: true },
]) {
  test(`initial layout shift stays below 0.05 on ${path}${queue ? ' with a proposal' : ''}`, async ({
    page,
  }, info) => {
    await syntheticGateway(page, { slow: true, queue })
    await page.addInitScript(() => {
      const shifts: { value: number; sources: string[] }[] = []
      Object.assign(window, { t84Shifts: shifts })
      new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) {
          const shift = entry as PerformanceEntry & {
            value: number
            hadRecentInput: boolean
            sources?: { node?: Element }[]
          }
          if (!shift.hadRecentInput)
            shifts.push({
              value: shift.value,
              sources: shift.sources?.map((s) => s.node?.className ?? '') ?? [],
            })
        }
      }).observe({ type: 'layout-shift', buffered: true })
    })
    await page.goto(path)
    await expect(page.locator('[aria-busy="true"]')).toHaveCount(0, { timeout: 12000 })
    // Include font readiness and subsequent paints; do not stop the observer at the first response.
    await page.evaluate(async () => {
      await document.fonts.ready
      await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)))
    })
    const shifts = await page.evaluate(
      () => (window as unknown as { t84Shifts: { value: number }[] }).t84Shifts,
    )
    await info.attach('layout-shifts.json', {
      body: JSON.stringify({ path, shifts }),
      contentType: 'application/json',
    })
    expect(shifts.reduce((sum, shift) => sum + shift.value, 0)).toBeLessThanOrEqual(0.05)
  })
}

test('reduced motion disables skeleton, spinner, button and result animations', async ({
  page,
}) => {
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await syntheticGateway(page, { slow: true, queue: true })
  await page.goto('/approver')
  await expect(page.locator('.skeleton').first()).toBeVisible()
  const assertStill = async () => {
    const animated = await page
      .locator('.skeleton, .spin, .btn, .approval-state, .step-content')
      .evaluateAll((elements) =>
        elements
          .filter((element) => {
            const style = getComputedStyle(element)
            return (
              style.animationName !== 'none' ||
              style.transitionDuration.split(',').some((duration) => parseFloat(duration) > 0)
            )
          })
          .map((element) => element.className),
      )
    expect(animated).toEqual([])
  }
  await assertStill()
  await page.getByRole('button', { name: 'Approve €506.16' }).click({ timeout: 15000 })
  await expect(page.locator('.spin')).toBeVisible()
  await assertStill()
  await expect(page.locator('.approval-state-done')).toBeVisible()
  await assertStill()
})

test('approve keeps the button busy while the server rechecks, then reveals confirmation', async ({
  page,
}) => {
  await syntheticGateway(page, { queue: true })
  await page.goto('/approver')
  const approve = page.getByRole('button', { name: 'Approve €506.16' })
  await approve.click()
  await expect(approve).toHaveAttribute('aria-busy', 'true')
  await expect(approve).toBeDisabled()
  await expect(approve.locator('.spin')).toBeVisible()
  await expect(page.getByText('Re-checking live price & availability…')).toBeVisible()
  await expect(page.locator('.approval-state-done')).toBeVisible()
})

test('loading errors remain visible instead of a permanent skeleton or empty queue', async ({
  page,
}) => {
  await syntheticGateway(page, { error: true })
  await page.goto('/approver')
  await expect(page.getByRole('alert')).toContainText('Could not load the approver queue.', {
    timeout: 15000,
  })
  await expect(page.getByText('Nothing to approve.')).toHaveCount(0)
  await expect(page.locator('.skeleton')).toHaveCount(0)
})

test('NDJSON completions advance the active step without invented timing or counts', async ({
  page,
}) => {
  await syntheticGateway(page)
  await page.addInitScript(() => {
    const original = window.fetch.bind(window)
    window.fetch = (input, init) => {
      if (String(input).endsWith('/v0.1/agent/run')) {
        const stream = new ReadableStream({
          start(controller) {
            const handler = (event: Event) => {
              const row = (event as CustomEvent).detail
              controller.enqueue(new TextEncoder().encode(JSON.stringify(row) + '\n'))
              if (row.type === 'result') {
                controller.close()
                window.removeEventListener('t84-step', handler)
              }
            }
            window.addEventListener('t84-step', handler)
          },
        })
        return Promise.resolve(
          new Response(stream, { headers: { 'Content-Type': 'application/x-ndjson' } }),
        )
      }
      return original(input, init)
    }
  })
  await page.goto('/')
  await expect(page.getByText('Buyer mandate', { exact: true })).toBeVisible()
  await page.getByLabel('What do you need, and by when?').fill(proposal.request.requestText)
  await page.getByRole('button', { name: 'Run request' }).click()
  const active = page.locator('.step[aria-current="step"]')
  await expect(active).toContainText('Reading the request…')
  await expect(page.getByRole('button', { name: 'Running…' }).locator('.spin')).toBeVisible()
  const send = (row: unknown) =>
    page.evaluate((row) => window.dispatchEvent(new CustomEvent('t84-step', { detail: row })), row)
  await send({
    type: 'step',
    step: 1,
    name: 'REQUEST',
    title: 'Request',
    status: 'done',
    request: proposal.request,
    summary: '2 items · structured',
  })
  await expect(active).toContainText('Checking price & stock for the shortlist…')
  await expect(page.locator('.step-done').first()).toContainText('Done')
  await send({
    type: 'step',
    step: 2,
    name: 'LOOKUP',
    title: 'Lookup',
    status: 'done',
    summary: 'Shortlist checked',
    plan: [{ item: proposal.request.items[0], shortlistIds: ['synthetic-1', 'synthetic-2'] }],
  })
  await expect(active).toContainText('Building the fixed-price proposal…')
  await send({
    type: 'step',
    step: 3,
    name: 'ORDER',
    title: 'Order',
    status: 'done',
    summary: 'Proposal ready',
  })
  await expect(active).toContainText('Deciding against the buyer mandate…')
  await send({
    type: 'result',
    customerId: proposal.customerId,
    steps: [],
    request: proposal.request,
    proposal,
    mode: 'RECORDED',
    reasons: [],
    explanation: {
      text: 'Human approval is required.',
      sentenceCount: 1,
      reasons: [],
      usedLlm: false,
    },
  })
  await expect(page.getByRole('button', { name: 'Run request' })).toBeEnabled()
  await expect(page.locator('.primary-result')).toBeVisible()
})
