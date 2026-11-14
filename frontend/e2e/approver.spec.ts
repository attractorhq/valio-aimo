import { expect, test, type Page } from '@playwright/test'
import jsQR from 'jsqr'

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
  await approver.getByLabel('Signed in as').selectOption('mock-school')
  await expect(approver.getByRole('heading', { name: 'Approver — Head Chef' })).toBeVisible()
  const card = approver.getByRole('article')
  await expect(card.getByRole('heading')).toHaveText('Approving version 1')
  await expect(card).toContainText(
    '€506.16 is above your €500.00 auto-approve limit. Everything else is verified or declared.',
  )
  await expect(card).toContainText('Synthetic gluten-free macaroni 5 kg')
  await expect(card).toContainText('18 bag')
  await expect(card).toContainText('Mon 16 Nov')
  await expect(card).toContainText('Single-use approval link')
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
    /Approved · re-checked \(recorded data\) · Confirmed · logged \d{2}:\d{2}/,
  )
  await expect(card.getByRole('button', { name: /Approve/ })).toHaveCount(0)
  await approver.screenshot({
    path: `test-results/approver-approved-${info.project.name}.png`,
    fullPage: true,
  })

  await page.bringToFront()
  await expect(step(page, 'Approval')).toContainText('Approved by Head Chef → Confirmed')
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
  await approver.getByLabel('Signed in as').selectOption('mock-school')
  await approver.getByLabel('Comment (optional)').fill('Too expensive this week.')
  await approver.getByRole('button', { name: 'Reject' }).click()
  await expect(approver.getByRole('article')).toContainText('Rejected · nothing was ordered')
  await page.bringToFront()
  await expect(step(page, 'Approval')).toContainText('Rejected by Head Chef')
})

test('a used approval link cannot be replayed', async ({ page }) => {
  await runScenarioB(page)
  await page.goto('/approver')
  await page.getByLabel('Signed in as').selectOption('mock-school')
  await page.getByRole('button', { name: 'Approve €506.16' }).click()
  await expect(page.getByRole('article')).toContainText('Confirmed')
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
  await page.getByLabel('Signed in as').selectOption('mock-school')
  const card = page.getByRole('article')
  await expect(card).toContainText('Expired · the offer ran out before sign-off')
  await expect(card.getByRole('button', { name: /Approve/ })).toHaveCount(0)
})

test('a superseded version can no longer be approved', async ({ page }) => {
  await runScenarioB(page)
  await patchPending(page, { state: 'SUPERSEDED', supersededBy: 'MOCK-P-B-v2' })
  await page.goto('/approver')
  await page.getByLabel('Signed in as').selectOption('mock-school')
  const card = page.getByRole('article')
  await expect(card).toContainText('Superseded · something changed at the re-check')
  await expect(card).toContainText('MOCK-P-B-v2')
  await expect(card.getByRole('button', { name: /Approve/ })).toHaveCount(0)
})

test('the approver panel is visibly not the agent and starts empty', async ({ page }) => {
  await page.goto('/approver')
  await expect(page.getByText('Human sign-off channel · not the agent')).toBeVisible()
  await expect(page.getByText('Demo sign-in: role only, no password')).toBeVisible()
  await expect(page.getByText('Nothing to approve.')).toHaveCount(0)
  await page.getByLabel('Signed in as').selectOption('mock-school')
  await expect(page.getByText('Nothing to approve.')).toBeVisible()
  await page.getByLabel('Signed in as').selectOption('mock-hotels')
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Approver — Purchasing Manager')
})

test('the approver panel has no horizontal page scroll', async ({ page }) => {
  await runScenarioB(page)
  await page.goto('/approver')
  await page.getByLabel('Signed in as').selectOption('mock-school')
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
  await page.getByLabel('Signed in as').selectOption('mock-school')
  const qr = page.getByRole('img', {
    name: 'Scan on a phone to open the single-use approval page',
  })
  await expect(qr).toBeVisible()
  const mobileHref = await page
    .getByRole('link', { name: 'Open mobile approval page' })
    .getAttribute('href')
  expect(mobileHref).toMatch(/^\/approve\/tk-B-/)
  const expectedAbsoluteUrl = new URL(mobileHref!, page.url()).href

  // Rasterize the rendered QR SVG onto a canvas and decode it with jsQR to prove a real phone camera can scan it.
  const imageData = await qr.evaluate(async (svgEl) => {
    const xml = new XMLSerializer().serializeToString(svgEl)
    const blob = new Blob([xml], { type: 'image/svg+xml;charset=utf-8' })
    const blobUrl = URL.createObjectURL(blob)
    try {
      const img = new Image()
      await new Promise<void>((resolve, reject) => {
        img.onload = () => resolve()
        img.onerror = () => reject(new Error('Failed to load SVG image'))
        img.src = blobUrl
      })
      const scale = 512
      const canvas = document.createElement('canvas')
      canvas.width = scale
      canvas.height = scale
      const ctx = canvas.getContext('2d')!
      ctx.fillStyle = '#ffffff'
      ctx.fillRect(0, 0, scale, scale)
      ctx.imageSmoothingEnabled = false
      ctx.drawImage(img, 0, 0, scale, scale)
      const pixels = ctx.getImageData(0, 0, scale, scale)
      return {
        data: Array.from(pixels.data),
        width: pixels.width,
        height: pixels.height,
      }
    } finally {
      URL.revokeObjectURL(blobUrl)
    }
  })
  const decoded = jsQR(new Uint8ClampedArray(imageData.data), imageData.width, imageData.height)
  expect(decoded).not.toBeNull()
  expect(decoded!.data).toBe(expectedAbsoluteUrl)

  // Open on a separate browser context (simulating a judge's phone on mobile data, 390 px wide).
  const phoneContext = await browser.newContext({ viewport: { width: 390, height: 844 } })
  const phone = await phoneContext.newPage()
  await phone.goto(decoded!.data)

  await expect(phone.getByRole('heading', { name: 'Approver — Head Chef' })).toBeVisible()
  const card = phone.getByRole('article')
  await expect(card.getByRole('heading')).toHaveText('Approving version 1')
  await expect(card).toContainText(
    '€506.16 is above your €500.00 auto-approve limit. Everything else is verified or declared.',
  )
  await expect(card).toContainText('Synthetic gluten-free macaroni 5 kg')
  await expect(card).toContainText('Single-use approval link')

  const overflow = await phone.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth,
  )
  expect(overflow).toBeLessThanOrEqual(0)

  await card.getByRole('button', { name: 'Approve €506.16' }).click()
  await expect(card).toContainText(
    /Approved · re-checked \(recorded data\) · Confirmed · logged \d{2}:\d{2}/,
  )
  await phoneContext.close()
})

test('B2 partial-delivery proposal shows "Approving means 95 kg instead of 120 kg, on Monday instead of today." in Approver panel and on /approve/:token', async ({
  page,
}) => {
  await runScenarioB(page)
  await page.evaluate((key) => {
    const store = JSON.parse(localStorage.getItem(key) ?? '{}')
    for (const r of Object.values(store) as Array<{
      proposal: {
        requestedDate: string
        createdAt: string
        reasons: string[]
        request: {
          requestText: string
          requestedDate: string
          items: Array<{ itemId: string; quantity: string; unit: string }>
        }
        lines: Array<{
          itemId: string
          quantitySalesUnits: string
          deliveredQuantity: { value: string; unit: string }
          deliveryDate: string
        }>
      }
    }>) {
      r.proposal.requestedDate = '2026-11-13'
      r.proposal.createdAt = '2026-11-13T21:52:02+02:00'
      r.proposal.reasons = ['DATE_LATER', 'PARTIAL_AVAILABILITY', 'OVER_AUTO_LIMIT']
      r.proposal.request.requestText =
        'Unexpected event: we need 120 kg of gluten-free macaroni and 60 litres of gluten-free oat drink today.'
      r.proposal.request.requestedDate = '2026-11-13'
      r.proposal.request.items[0].quantity = '120'
      r.proposal.lines[0].quantitySalesUnits = '19'
      r.proposal.lines[0].deliveredQuantity = { value: '95', unit: 'kg' }
      r.proposal.lines[0].deliveryDate = '2026-11-16'
    }
    localStorage.setItem(key, JSON.stringify(store))
  }, STORE)

  await page.goto('/approver')
  await page.getByLabel('Signed in as').selectOption('mock-school')
  const card = page.getByRole('article')
  await expect(card.getByRole('heading')).toHaveText('Approving version 1')
  const statement = card.getByRole('note', { name: 'Approval statement' })
  await expect(statement).toHaveText(
    'Approving means 95 kg instead of 120 kg, on Monday instead of today.',
  )

  const mobileHref = await card
    .getByRole('link', { name: 'Open mobile approval page' })
    .getAttribute('href')
  await page.goto(mobileHref!)
  await expect(page.getByRole('note', { name: 'Approval statement' })).toHaveText(
    'Approving means 95 kg instead of 120 kg, on Monday instead of today.',
  )
})

test('a queue row shows the customer, the products and each reason once', async ({ page }) => {
  await runScenarioB(page)
  await page.goto('/approver')
  await page.getByLabel('Signed in as').selectOption('mock-school')
  const row = page.locator('.approver-queue button').filter({ hasText: '€506.16' }).first()
  await expect(row).toContainText('Synthetic School Kitchen (demo mock)')
  await expect(row.getByTestId('queue-products')).toHaveText(
    'Synthetic gluten-free macaroni 5 kg, Synthetic gluten-free oat drink 1 l',
  )
  await expect(row.getByText('Above auto-approve limit', { exact: true })).toHaveCount(1)
  await expect(row.getByTestId('queue-statement')).toHaveCount(0)
})

test('T69 split top-up proposal renders both lines, the approval sentence, and the price-guard detail line', async ({
  page,
}) => {
  await runScenarioB(page)
  await page.evaluate((key) => {
    const store = JSON.parse(localStorage.getItem(key) ?? '{}')
    for (const r of Object.values(store) as Array<{
      proposal: {
        reasons: string[]
        request: {
          items: Array<{ itemId: string; what: string; quantity: string; unit: string }>
        }
        lines: Array<{
          lineId: string
          itemId: string
          productId: string
          name: string
          salesUnit: string
          salesUnitLabel: string
          contentPerSalesUnit: { value: string; unit: string; level: string; method: string }
          quantitySalesUnits: string
          deliveredQuantity: { value: string; unit: string }
          unitPriceExclVat: string
          lineTotalExclVat: string
          vatRate: string
          deliveryDate: string
          closingTime: string | null
          facts: unknown[]
          reasons: string[]
          substitutionSource?: string
          substituteFor?: string
        }>
      }
    }>) {
      r.proposal.reasons = ['SPLIT', 'AGENT_SUBSTITUTE']
      r.proposal.request.items = [
        { itemId: 'I1', what: 'gluten-free pasta', quantity: '120', unit: 'kg' },
      ]
      const baseLine = r.proposal.lines[0]
      r.proposal.lines = [
        {
          ...baseLine,
          lineId: 'L1',
          itemId: 'I1',
          productId: 'SYN-A',
          name: 'A',
          contentPerSalesUnit: { value: '5', unit: 'kg', level: 'DECLARED', method: 'salesUnit' },
          quantitySalesUnits: '14',
          deliveredQuantity: { value: '70', unit: 'kg' },
          unitPriceExclVat: '10.00',
          lineTotalExclVat: '140.00',
          reasons: [],
        },
        {
          ...baseLine,
          lineId: 'L2',
          itemId: 'I1',
          productId: 'SYN-B',
          name: 'B',
          contentPerSalesUnit: { value: '5', unit: 'kg', level: 'DECLARED', method: 'salesUnit' },
          quantitySalesUnits: '10',
          deliveredQuantity: { value: '50', unit: 'kg' },
          unitPriceExclVat: '12.00',
          lineTotalExclVat: '120.00',
          reasons: ['SPLIT', 'AGENT_SUBSTITUTE'],
          substitutionSource: 'AGENT',
          substituteFor: 'SYN-A',
        },
      ]
    }
    localStorage.setItem(key, JSON.stringify(store))
  }, STORE)

  await page.goto('/approver')
  await page.getByLabel('Signed in as').selectOption('mock-school')
  const card = page.getByRole('article')
  await expect(card).toContainText('SYN-A')
  await expect(card).toContainText('70 kg')
  await expect(card).toContainText('SYN-B')
  await expect(card).toContainText('50 kg')
  const statement = card.getByRole('note', { name: 'Approval statement' })
  await expect(statement).toContainText('Approving means 70 kg of A + 50 kg of B (€0.40/kg more).')
  await expect(statement).toContainText('+20 % per kg')
})
