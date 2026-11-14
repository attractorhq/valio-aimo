import { expect, test, type Page } from '@playwright/test'

const step = (page: Page, name: string) => page.getByRole('listitem', { name: new RegExp(name) })

async function runScenario(page: Page, id: 'A' | 'B' | 'C') {
  await page.getByRole('button', { name: new RegExp(`^Scenario ${id}:`) }).click()
  await expect(step(page, 'Approval')).toHaveAttribute('aria-current', 'step')
}

test.beforeEach(async ({ page }) => {
  await page.goto('/')
  await expect(page.getByText('Synthetic mock data')).toBeVisible()
})

test('shows one main view with four steps and a read-only mandate', async ({ page }) => {
  for (const name of ['Request', 'Lookup', 'Order', 'Approval']) {
    await expect(step(page, name)).toContainText('Not started')
  }
  const mandate = page.getByRole('region', { name: 'Buyer mandate' })
  await expect(mandate).toContainText('Read-only')
  await expect(mandate).toContainText('€1,500.00 excl. VAT')
  await mandate.getByText('Synthetic School Kitchen (demo mock)').click()
  await expect(mandate).toContainText('€500.00 excl. VAT')
  await expect(mandate).toContainText('€3,000.00 excl. VAT')
  await expect(mandate).toContainText('Strict')
  await expect(mandate).toContainText('Head Chef (demo)')
  // T46: the buyer page has no mandate write controls.
  await expect(page.getByRole('button', { name: 'Apply policy change' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Reset to default mandate' })).toHaveCount(0)
  await expect(page.getByLabel('Auto-approve limit (€, excl. VAT)')).toHaveCount(0)
  await expect(page.getByRole('heading', { name: 'Mandate v1 · read-only' })).toBeVisible()
  await expect(page.getByRole('link', { name: 'Buyer admin (demo, no login)' })).toHaveAttribute(
    'href',
    '/admin',
  )
})

test('scenario A: agent decided → confirmed (simulated)', async ({ page }, info) => {
  await runScenario(page, 'A')
  await expect(step(page, 'Request')).toContainText('Lactose-free')
  await expect(step(page, 'Request')).toContainText('Mon 16 Nov')
  await expect(step(page, 'Order')).toContainText(
    'Proposal at fixed price · offer v1 · €1,010.00 excl. VAT',
  )
  const altsA = step(page, 'Order').getByRole('region', {
    name: 'Good / Better / Best alternatives',
  })
  await expect(altsA).toBeVisible()
  await expect(altsA).toContainText('Recommended')
  await expect(altsA).toContainText('Best')
  await expect(altsA).toContainText('Better')
  await expect(altsA).toContainText('Good')
  await expect(altsA).toContainText('Lactose-free proven')
  await altsA.getByRole('button', { name: 'Show trade-offs' }).click()
  await expect(altsA).toContainText('+€0.09/l vs recommended')
  await expect(altsA).toContainText('plenty in stock (verified)')
  const approval = step(page, 'Approval')
  await expect(approval).toContainText('Agent decided → Confirmed (simulated)')
  await expect(approval).toContainText('Simulated order — mock environment')
  await expect(page.getByText(/Recorded · captured Fri 13 Nov 21:50/)).toBeVisible()
  await page.screenshot({
    path: `test-results/scenario-a-${info.project.name}.png`,
    fullPage: true,
  })
})

test('scenario B: waiting for approver, exactly one reason, no approve button', async ({
  page,
}, info) => {
  await runScenario(page, 'B')
  await expect(page.getByRole('radio', { name: /School Kitchen/ })).toBeChecked()
  await expect(step(page, 'Order')).toContainText('offer v1 · €506.16 excl. VAT')
  const altsB = step(page, 'Order').getByRole('region', {
    name: 'Good / Better / Best alternatives',
  })
  await expect(altsB).toBeVisible()
  await expect(altsB).toContainText('Gluten-free proven')
  await expect(altsB).toContainText('Recommended')
  await expect(altsB).toContainText('Best')
  await expect(altsB).toContainText('Better')
  await expect(altsB).not.toContainText('MOCK-REJECTED')
  const approval = step(page, 'Approval')
  await expect(approval).toContainText('Waiting for approver')
  await expect(approval).toContainText('Waiting for Head Chef (demo)')
  await expect(approval.getByRole('list', { name: 'Reasons' }).getByRole('listitem')).toHaveCount(1)
  await expect(approval).toContainText(
    '€506.16 excl. VAT is above your €500.00 auto-approve limit.',
  )
  await expect(page.getByRole('button', { name: /approve/i })).toHaveCount(0)
  await expect(step(page, 'Lookup')).toContainText('excluded')
  await page.screenshot({
    path: `test-results/scenario-b-${info.project.name}.png`,
    fullPage: true,
  })
})

test('scenario C: blocked by mandate, instruction ignored', async ({ page }, info) => {
  await runScenario(page, 'C')
  const approval = step(page, 'Approval')
  await expect(approval).toContainText('Blocked by mandate')
  await expect(approval).toContainText('Above hard limit')
  await expect(approval).toContainText('€20,200.00 excl. VAT is above your €5,000.00 hard limit.')
  await expect(approval).toContainText('Instruction ignored')
  await page.screenshot({
    path: `test-results/scenario-c-${info.project.name}.png`,
    fullPage: true,
  })
})

test('free text in mock mode says so instead of inventing a result', async ({ page }) => {
  await page.getByLabel('What do you need, and by when?').fill('5 kg of butter by Friday')
  await page.getByRole('button', { name: 'Run request' }).click()
  await expect(page.getByRole('alert')).toContainText('Free-text requests need the live gateway')
  await expect(step(page, 'Approval')).toContainText('Not started')
})

test('has no horizontal page scroll', async ({ page }) => {
  await runScenario(page, 'B')
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth,
  )
  expect(overflow).toBeLessThanOrEqual(0)
})

test('collapsible trust cards keep step 4 reachable within one viewport scroll and expand on demand', async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await runScenario(page, 'B')

  // Trust cards start collapsed, showing summary counts, hard requirements and the Valio-substitute check.
  const toggles = page.getByRole('button', { name: /Show facts/ })
  await expect(toggles).toHaveCount(2)
  await expect(toggles.first()).toHaveAttribute('aria-expanded', 'false')
  await expect(page.getByRole('note', { name: 'Valio-substitute check' })).toBeVisible()

  // At 1440x900, the Approval outcome is reachable within at most one viewport (900 px) of scrolling.
  const approvalTop = await step(page, 'Approval').evaluate(
    (el) => el.getBoundingClientRect().top + window.scrollY,
  )
  // T43 puts a result banner (decision, total, lines) at the top, so step 4 sits slightly lower.
  expect(approvalTop).toBeLessThanOrEqual(1900)

  // Expanding a trust card reveals every fact with its badge, source and timestamp.
  await toggles.first().click()
  const hideBtn = page.getByRole('button', { name: 'Hide facts' })
  await expect(hideBtn).toHaveAttribute('aria-expanded', 'true')
  const pastaCard = page.getByRole('article', {
    name: /Trust card for Synthetic gluten-free macaroni 5 kg/,
  })
  await expect(pastaCard).toContainText('valio-aimo.price-api · catalogPrice.value')
  await expect(pastaCard).toContainText('valio-aimo.availability-api · closingTime')
  await expect(pastaCard).toContainText('Fri 13 Nov 21:50')
})

test('editable assumption chips: changing "Any fat %" to "1.5 %" in Scenario A re-runs the flow and changes the shortlist', async ({
  page,
}) => {
  await runScenario(page, 'A')
  await expect(step(page, 'Lookup')).toContainText('Shortlist (2): MOCK-MILK, MOCK-MILK-3PCT')

  await step(page, 'Request').getByRole('button', { name: 'Edit assumption: Any fat %' }).click()
  const input = step(page, 'Request').getByRole('textbox', { name: 'Edit assumption Any fat %' })
  await input.fill('1.5 %')
  await step(page, 'Request').getByRole('button', { name: 'Apply assumption Any fat %' }).click()

  await expect(step(page, 'Request')).toContainText('Preferences: 1.5 %')
  await expect(
    step(page, 'Request').getByRole('button', { name: 'Edit assumption: 1.5 %' }),
  ).toBeVisible()
  await expect(step(page, 'Lookup')).toContainText('Shortlist (1): MOCK-MILK')
  await expect(step(page, 'Lookup')).not.toContainText('MOCK-MILK-3PCT')
})

test('Valio Aimo readiness view shows supplier data gaps, AUTO/ASK/BLOCK statistics and fix list computed from data', async ({
  page,
}) => {
  await page.getByRole('link', { name: 'Valio Aimo readiness' }).click()
  await expect(
    page.getByRole('heading', {
      name: /Valio Aimo Agent-Readiness View · Data quality = autonomous sales/,
    }),
  ).toBeVisible()

  const summary = page.getByRole('region', { name: /Valio Aimo Agent-Readiness View/ })
  await expect(summary).toContainText('AUTO-Ready Products')
  await expect(summary).toContainText('Blocked from AUTO')

  const liveStats = page.getByRole('region', {
    name: 'Live AUTO / ASK / BLOCK Decision Statistics',
  })
  await expect(liveStats).toContainText('AUTO · Autonomous Order')
  await expect(liveStats).toContainText('ASK · Human Sign-Off')
  await expect(liveStats).toContainText('BLOCK · Mandate Refusal')
  await expect(liveStats).toContainText('€1,010.00')
  await expect(liveStats).toContainText('€506.16')
  await expect(liveStats).toContainText('€20,200.00')

  const suppliersSection = page.getByRole('region', { name: 'Data Gaps by Supplier' })
  await expect(suppliersSection).toContainText('SYNTHETIC DAIRY OY')
  await expect(suppliersSection).toContainText('SYNTHETIC NORDIC PASTA')
  await suppliersSection.getByLabel('Filter suppliers by name').fill('NORDIC PASTA')
  await expect(suppliersSection).toContainText('SYNTHETIC NORDIC PASTA')
  await expect(suppliersSection).not.toContainText('SYNTHETIC NONFOOD SUPPLIES')

  const fixList = page.getByRole('region', { name: 'Prioritised Master-Data Fix List' })
  await expect(fixList).toContainText('SUBSTITUTE_REJECTED')
  await expect(fixList).toContainText('DATA_CONFLICT')
  await expect(fixList).toContainText('ALLERGEN_UNKNOWN')
})

test('T46: /admin holds buyer-admin controls and reset; buyer page shows mandate version and changed-from-default read-only', async ({
  page,
}) => {
  const defaultM2 = {
    customerId: 'northbay-school-kitchen',
    version: 1,
    organisationName: 'Northbay School Kitchen (demo)',
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
    approver: { name: 'Head Chef (demo)', role: 'Head Chef (demo)' },
  }
  let currentM2 = { ...defaultM2 }
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
    if (url.pathname === '/v0.1/mandates') {
      return route.fulfill(json([currentM2]))
    }
    if (url.pathname === '/v0.1/admin/mandates/northbay-school-kitchen/default') {
      const diffs =
        currentM2.autoApproveLimitEur !== defaultM2.autoApproveLimitEur
          ? [
              {
                field: 'autoApproveLimitEur',
                current: currentM2.autoApproveLimitEur,
                default: defaultM2.autoApproveLimitEur,
              },
            ]
          : []
      return route.fulfill(
        json({
          mandate: currentM2,
          default: defaultM2,
          changedFromDefault: diffs.length > 0,
          differences: diffs,
        }),
      )
    }
    if (
      url.pathname === '/v0.1/admin/mandates/northbay-school-kitchen' &&
      req.method() === 'PATCH'
    ) {
      const body = JSON.parse(req.postData() ?? '{}') as { autoApproveLimitEur?: string }
      currentM2 = {
        ...currentM2,
        version: currentM2.version + 1,
        autoApproveLimitEur: body.autoApproveLimitEur ?? currentM2.autoApproveLimitEur,
      }
      return route.fulfill(
        json({
          mandate: currentM2,
          auditEntry: {
            entryId: `mandate-northbay-school-kitchen-v${currentM2.version}`,
            customerId: currentM2.customerId,
            mandateVersion: currentM2.version,
            timestamp: '2026-11-13T21:55:00+02:00',
            actor: 'human:Buyer admin (demo)',
            event: 'MANDATE_CHANGED',
            rule: '§6.1',
            summary: 'Policy changed by buyer admin',
          },
          superseded: [],
          redecided: [],
        }),
      )
    }
    if (
      url.pathname === '/v0.1/admin/mandates/northbay-school-kitchen/reset' &&
      req.method() === 'POST'
    ) {
      currentM2 = {
        ...defaultM2,
        version: currentM2.version + 1,
      }
      return route.fulfill(
        json({
          mandate: currentM2,
          auditEntry: {
            entryId: `mandate-northbay-school-kitchen-v${currentM2.version}`,
            customerId: currentM2.customerId,
            mandateVersion: currentM2.version,
            timestamp: '2026-11-13T21:56:00+02:00',
            actor: 'human:Buyer admin (demo)',
            event: 'MANDATE_RESET',
            rule: '§6.1',
            summary: 'Policy reset to default by buyer admin',
          },
          superseded: [],
          redecided: [],
        }),
      )
    }
    return route.fulfill(json({ error: { code: 'NOT_FOUND', message: 'not faked' } }, 404))
  })

  await page.goto('/')
  await expect(page.getByRole('button', { name: 'Apply policy change' })).toHaveCount(0)
  await page.getByRole('link', { name: 'Buyer admin (demo, no login)' }).click()
  await expect(page).toHaveURL(/\/admin$/)
  await expect(page.getByRole('heading', { name: 'Buyer admin (demo, no login)' })).toBeVisible()

  const limitInput = page.getByLabel('Auto-approve limit (€, excl. VAT)')
  await expect(limitInput).toBeEnabled()
  await limitInput.fill('510.00')
  await page.getByRole('button', { name: 'Apply policy change' }).click()

  await expect(page.getByRole('status')).toContainText(
    'Policy changed by buyer admin · mandate v2 · auto-approve limit €510.00',
  )
  await expect(page.getByRole('note')).toContainText(
    'Changed from default: auto-approve €510.00 (default €500.00)',
  )
  await expect(page.getByRole('button', { name: 'Reset to default mandate' })).toBeVisible()

  // Navigate back to the buyer view: read-only, shows v2 and "Changed from default", no write controls.
  await page.getByRole('link', { name: 'Back to the buyer view' }).click()
  await expect(page).toHaveURL(/\/$/)
  await expect(page.getByRole('heading', { name: 'Mandate v2 · read-only' })).toBeVisible()
  await expect(page.getByRole('note')).toContainText(
    'Changed from default: auto-approve €510.00 (default €500.00)',
  )
  await expect(page.getByRole('button', { name: 'Apply policy change' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Reset to default mandate' })).toHaveCount(0)
  await expect(page.getByLabel('Auto-approve limit (€, excl. VAT)')).toHaveCount(0)

  // Return to /admin and reset to the shipped default (v3).
  await page.getByRole('link', { name: 'Buyer admin (demo, no login)' }).click()
  await page.getByRole('button', { name: 'Reset to default mandate' }).click()
  await expect(page.getByRole('status')).toContainText(
    'Policy reset to default by buyer admin · mandate v3 · auto-approve limit €500.00',
  )
})
