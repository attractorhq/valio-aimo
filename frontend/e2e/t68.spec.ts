import { expect, test, type Page } from '@playwright/test'

// T68: the mandate as a plain promise (alone / needs the approver / always refused), computed from the mandate.

const M2 = {
  customerId: 'northbay-school-kitchen',
  version: 1,
  organisationName: 'Northbay School Kitchen',
  segment: 'school kitchen',
  allergenMode: 'STRICT' as const,
  autoApproveLimitEur: '500.00',
  hardLimitEur: '3000.00',
  autoAcceptValioSubstitutes: false,
  autoAcceptAgentSubstitutes: false,
  autoAcceptSupplierStock: false,
  minReliability: '0.80',
  enforceCutoff: true,
  forbiddenAllergens: [] as string[],
  approver: { name: 'Head Chef', role: 'Head Chef' },
  allowedCategories: [] as string[],
  validFrom: null as string | null,
  validTo: null as string | null,
}

const summary = (page: Page) => page.getByLabel('Mandate in plain words')

async function fakeGateway(page: Page) {
  let current = { ...M2 }
  const json = (body: unknown, status = 200) => ({
    status,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  await page.route('**/health', (route) =>
    route.fulfill(json({ status: 'ok', ready: true, mode: 'RECORDED', version: '0.1' })),
  )
  await page.route('**/v0.1/**', async (route) => {
    const req = route.request()
    const url = new URL(req.url())
    if (url.pathname === '/v0.1/mandates') return route.fulfill(json([current]))
    if (url.pathname === `/v0.1/admin/mandates/${M2.customerId}/default`) {
      return route.fulfill(
        json({ mandate: current, default: M2, changedFromDefault: false, differences: [] }),
      )
    }
    if (url.pathname === `/v0.1/admin/mandates/${M2.customerId}` && req.method() === 'PATCH') {
      const body = JSON.parse(req.postData() ?? '{}')
      current = { ...current, ...body, version: current.version + 1 }
      return route.fulfill(
        json({
          mandate: current,
          auditEntry: {
            entryId: `mandate-${M2.customerId}-v${current.version}`,
            customerId: M2.customerId,
            mandateVersion: current.version,
            timestamp: '2026-11-13T21:55:00+02:00',
            actor: 'human:Buyer admin',
            event: 'MANDATE_CHANGED',
            rule: '§6.1',
            summary: 'Policy changed by buyer admin',
          },
          superseded: [],
          redecided: [],
        }),
      )
    }
    return route.fulfill(json({ error: { code: 'NOT_FOUND', message: 'not faked' } }, 404))
  })
}

test('T68: the plain-language mandate summary is on the main view (offline examples too)', async ({
  page,
}) => {
  await page.goto('/')
  await expect(summary(page)).toBeVisible()
  await expect(summary(page)).toContainText('Agent may order alone')
  await expect(summary(page)).toContainText('Always refused')
})

test('T68: summary on the main view and /admin follows a buyer-admin edit', async ({ page }) => {
  await fakeGateway(page)
  await page.goto('/')
  await expect(summary(page)).toContainText(
    'Agent may order aloneIn-stock, verified products up to €500.00 per delivery day',
  )
  await expect(summary(page)).toContainText('Needs the Head ChefOver €500.00, later delivery')
  await expect(summary(page)).toContainText('Always refusedOver €3,000.00')
  await expect(summary(page)).toContainText('missing evidence')

  await page.goto('/admin')
  await expect(summary(page)).toContainText('up to €500.00 per delivery day')
  await page.getByRole('button', { name: 'Enter admin key', exact: true }).click()
  await page.getByLabel('Auto-approve limit (€, excl. VAT)').fill('750.00')
  await page.getByLabel('Allowed categories').fill('211, 2240')
  await page.getByLabel('Until').fill('2026-12-31')
  await page.getByRole('button', { name: 'Apply policy change' }).click()

  await expect(page.getByRole('status')).toContainText('mandate v2')
  await expect(summary(page)).toContainText(
    'In-stock, verified products from categories 211, 2240 up to €750.00 per delivery day, until Thu 31 Dec',
  )
  await expect(summary(page)).toContainText('Needs the Head ChefOver €750.00')
  await expect(summary(page)).toContainText('orders after Thu 31 Dec, other categories')

  await page.getByRole('link', { name: 'Buyer view' }).click()
  await expect(summary(page)).toContainText('up to €750.00 per delivery day, until Thu 31 Dec')
})
