import { expect, test, type Page } from '@playwright/test'

// #174: buyer admin saves, reloads, edits and clears free-text restaurant preferences, per customer.

const base = {
  version: 1,
  allergenMode: 'STANDARD' as const,
  autoApproveLimitEur: '1500.00',
  hardLimitEur: '5000.00',
  autoAcceptValioSubstitutes: true,
  autoAcceptAgentSubstitutes: false,
  autoAcceptSupplierStock: false,
  minReliability: '0.80',
  enforceCutoff: true,
  forbiddenAllergens: [] as string[],
  allowedCategories: [] as string[],
  validFrom: null,
  validTo: null,
  restaurantPreferences: '',
}
const HOTEL = {
  ...base,
  customerId: 'nordic-harbour-hotels',
  organisationName: 'Nordic Harbour Hotels',
  segment: 'hotel chain',
  approver: { name: 'Purchasing Manager', role: 'Purchasing Manager' },
}
const SCHOOL = {
  ...base,
  customerId: 'northbay-school-kitchen',
  organisationName: 'Northbay School Kitchen',
  segment: 'school kitchen',
  allergenMode: 'STRICT' as const,
  autoApproveLimitEur: '500.00',
  hardLimitEur: '3000.00',
  approver: { name: 'Head Chef', role: 'Head Chef' },
}
const CREAM = 'Our head chef prefers cream rather than milk for cooking and baking, when appropriate.'

async function fakeGateway(page: Page) {
  const store: Record<string, typeof HOTEL> = {
    [HOTEL.customerId]: { ...HOTEL },
    [SCHOOL.customerId]: { ...SCHOOL },
  }
  const patches: unknown[] = []
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
    const path = new URL(req.url()).pathname
    if (path === '/v0.1/mandates') return route.fulfill(json(Object.values(store)))
    const match = path.match(/^\/v0\.1\/admin\/mandates\/([^/]+)(\/default)?$/)
    if (match && match[2]) {
      const m = store[match[1]]
      return route.fulfill(json({ mandate: m, default: m, changedFromDefault: false, differences: [] }))
    }
    if (match && req.method() === 'PATCH') {
      const body = JSON.parse(req.postData() ?? '{}')
      patches.push(body)
      const m = store[match[1]]
      store[match[1]] = { ...m, ...body, version: m.version + 1 }
      return route.fulfill(
        json({
          mandate: store[match[1]],
          auditEntry: {
            entryId: `mandate-${match[1]}-v${m.version + 1}`,
            customerId: match[1],
            mandateVersion: m.version + 1,
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
  return patches
}

test('#174: save, reload, edit and clear restaurant preferences; each customer sees only its own', async ({
  page,
}) => {
  const patches = await fakeGateway(page)
  await page.goto('/admin')
  await page.getByRole('button', { name: 'Enter admin key', exact: true }).click()

  const field = page.getByLabel('Preferences for Nordic Harbour Hotels')
  await expect(field).toHaveValue('')
  await expect(page.getByRole('button', { name: 'Clear preferences' })).toBeDisabled()

  await field.fill(`  ${CREAM}  `)
  await page.getByRole('button', { name: 'Save preferences' }).click()
  await expect(page.getByRole('status').filter({ hasText: 'Preferences saved' })).toContainText(
    'Preferences saved · mandate v2',
  )
  expect(patches.at(-1)).toEqual({ restaurantPreferences: CREAM })

  await page.reload()
  await expect(page.getByLabel('Preferences for Nordic Harbour Hotels')).toHaveValue(CREAM)

  // The other customer has its own, empty preferences.
  await page.getByText('Northbay School Kitchen', { exact: true }).first().click()
  await expect(page.getByLabel('Preferences for Northbay School Kitchen')).toHaveValue('')

  await page.getByText('Nordic Harbour Hotels', { exact: true }).first().click()
  const hotel = page.getByLabel('Preferences for Nordic Harbour Hotels')
  await hotel.fill(`${CREAM} Finnish butter when available.`)
  await page.getByRole('button', { name: 'Save preferences' }).click()
  await expect(page.getByRole('status').filter({ hasText: 'Preferences saved' })).toContainText(
    'mandate v3',
  )

  // Too long: refused before it reaches the gateway.
  await hotel.fill('x'.repeat(1001))
  await expect(page.getByText('1001 / 1000 characters')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Save preferences' })).toBeDisabled()

  await page.getByRole('button', { name: 'Clear preferences' }).click()
  await expect(page.getByRole('status').filter({ hasText: 'Preferences cleared' })).toContainText(
    'Preferences cleared · mandate v4',
  )
  await expect(hotel).toHaveValue('')
  expect(patches.at(-1)).toEqual({ restaurantPreferences: '' })
})
