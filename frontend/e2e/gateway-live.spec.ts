import { expect, test, type Page } from '@playwright/test'
import { spawn, type ChildProcess } from 'node:child_process'
import path from 'node:path'

const step = (page: Page, name: string) => page.getByRole('listitem', { name: new RegExp(name) })

test.describe('live /v0.1 gateway integration (T35)', () => {
  test.setTimeout(120_000)

  let backendProc: ChildProcess | null = null
  let viteProc: ChildProcess | null = null
  const backendPort = 8018
  const vitePort = 5188
  const baseURL = `http://localhost:${vitePort}`

  test.beforeAll(async () => {
    const repoRoot = path.resolve(process.cwd(), '..')

    // Start a dedicated FastAPI server on :8018 backed by recorded synthetic/fixture state
    const pyScript = `
import os, uvicorn
from datetime import datetime
from test_agent_run import _build_synthetic_catalog, SyntheticAgentProvider, NOW
from app.api.state import DEFAULT_FIXTURES_DIR, GatewayState, get_state, latest_capture, try_state
from app.catalog.build import DEFAULT_ARTIFACT_PATH, load_catalog
from app.gateway.proposals import ProposalGateway
from app.main import app
from app.policy import DEMO_MANDATES, MandateBook
from app.valio import RecordedValioProvider

capture = latest_capture(DEFAULT_FIXTURES_DIR)
if capture is not None and DEFAULT_ARTIFACT_PATH.exists():
    cat = load_catalog()
    products = cat.by_id()
    catalog_list = list(cat.products)
    provider = RecordedValioProvider(capture)
    clock_fn = lambda: provider.recorded_at
else:
    products = _build_synthetic_catalog()
    catalog_list = list(products.values())
    provider = SyntheticAgentProvider(now=NOW)
    clock_fn = lambda: NOW

gateway = ProposalGateway(
    products=products,
    mandates=MandateBook(DEMO_MANDATES),
    provider=provider,
)
state = GatewayState(
    gateway=gateway,
    catalog=catalog_list,
    mode="RECORDED",
    clock=clock_fn,
)
app.dependency_overrides[get_state] = lambda: state
app.dependency_overrides[try_state] = lambda: state
uvicorn.run(app, host="127.0.0.1", port=${backendPort}, log_level="warning")
`
    const pyBin = path.join(repoRoot, '.venv/bin/python')
    backendProc = spawn(pyBin, ['-c', pyScript], {
      cwd: path.join(repoRoot, 'backend'),
      env: {
        ...process.env,
        PYTHONPATH: `${path.join(repoRoot, 'backend')}:${path.join(repoRoot, 'backend/tests')}`,
        OPENROUTER_API_KEY: '',
      },
      stdio: 'pipe',
    })

    // Wait for /health on backendPort
    for (let i = 0; i < 60; i++) {
      try {
        const res = await fetch(`http://127.0.0.1:${backendPort}/health`)
        if (res.ok) {
          const data = (await res.json()) as { ready?: boolean }
          if (data.ready) break
        }
      } catch {
        // retry
      }
      await new Promise((r) => setTimeout(r, 250))
    }

    viteProc = spawn(
      'npx',
      ['vite', '--host', '127.0.0.1', '--port', String(vitePort), '--strictPort'],
      {
        cwd: process.cwd(),
        env: {
          ...process.env,
          VITE_API_BASE_URL: `http://127.0.0.1:${backendPort}`,
        },
        stdio: 'pipe',
      },
    )

    for (let i = 0; i < 40; i++) {
      try {
        const res = await fetch(baseURL)
        if (res.ok) break
      } catch {
        // retry
      }
      await new Promise((r) => setTimeout(r, 200))
    }
  })

  test.afterAll(async () => {
    viteProc?.kill('SIGTERM')
    backendProc?.kill('SIGTERM')
  })

  test('runs official example, Scenario B with Approver sign-off, Scenario C, Finnish free text, clarification and decision log against /v0.1 gateway', async ({
    browser,
  }) => {
    const context = await browser.newContext({ viewport: { width: 1280, height: 900 } })
    const page = await context.newPage()

    await page.goto(baseURL)
    // Connected to real /v0.1 gateway: shows Recorded mode badge, NOT "Synthetic mock data"
    await expect(page.getByText(/Recorded · captured/)).toBeVisible()
    await expect(page.getByText('Synthetic mock data')).toHaveCount(0)

    const mandateRegion = page.getByRole('region', { name: 'Buyer mandate' })
    await expect(mandateRegion).toContainText('Nordic Harbour Hotels (demo)')
    await expect(mandateRegion).toContainText('Northbay School Kitchen (demo)')

    // 1. Official example typed in free text -> AUTO confirmed (€1,010.00)
    await page
      .getByLabel('What do you need, and by when?')
      .fill(
        'I need 1,000 litres of lactose-free milk by Monday. Find an option that meets our requirements based on price and availability.',
      )
    await page.getByRole('button', { name: 'Run request' }).click()
    await expect(step(page, 'Approval')).toContainText('Agent decided → Confirmed (simulated)')
    await expect(step(page, 'Order')).toContainText('€1,010.00 excl. VAT')
    await expect(step(page, 'Order')).toContainText('464306')
    await expect(step(page, 'Lookup')).toContainText('463335')

    // 1b. T24: Changing "Any fat %" to "1.5 %" in A re-runs the flow and changes the shortlist
    await step(page, 'Request').getByRole('button', { name: 'Edit assumption: Any fat %' }).click()
    const fatInput = step(page, 'Request').getByRole('textbox', { name: 'Edit assumption Any fat %' })
    await fatInput.fill('1.5 %')
    await step(page, 'Request').getByRole('button', { name: 'Apply assumption Any fat %' }).click()
    await expect(step(page, 'Request')).toContainText('Preferences: 1.5 %')
    await expect(
      step(page, 'Request').getByRole('button', { name: 'Edit assumption: 1.5 %' }),
    ).toBeVisible()
    await expect(step(page, 'Lookup')).toContainText('464306')
    await expect(step(page, 'Lookup')).not.toContainText('463335')

    // 2. Scenario B -> ASK -> Approver panel -> Approve -> CONFIRMED_SIMULATED in both views
    await page.getByRole('button', { name: /^Scenario B:/ }).click()
    await expect(step(page, 'Order')).toContainText('€506.16 excl. VAT')
    await expect(step(page, 'Approval')).toContainText('Waiting for approver')
    await expect(step(page, 'Approval')).toContainText('Waiting for Head Chef (demo)')
    await expect(page.getByRole('button', { name: /approve/i })).toHaveCount(0)

    const approverPage = await context.newPage()
    await approverPage.goto(`${baseURL}/approver`)
    await expect(
      approverPage.getByRole('heading', { name: 'Approver — Head Chef (demo)' }),
    ).toBeVisible()
    const card = approverPage.getByRole('article')
    await expect(card.getByRole('heading')).toHaveText(
      'You are approving version 1 of this proposal',
    )
    await expect(card).toContainText('€506.16 is above your €500.00 auto-approve limit.')
    const mobileHref = await card
      .getByRole('link', { name: 'Open mobile approval page' })
      .getAttribute('href')
    expect(mobileHref).toMatch(/^\/approve\/atk-.*\?proposalId=PR-/)

    // Verify phone approval page (/approve/:token) loads the exact proposal from the live gateway
    const phonePage = await context.newPage()
    await phonePage.goto(`${baseURL}${mobileHref}`)
    await expect(phonePage.getByRole('article')).toContainText(
      'You are approving version 1 of this proposal',
    )
    await phonePage.close()

    await card.getByRole('button', { name: 'Approve €506.16' }).click()
    await expect(card).toContainText(
      /Approved · re-checked \(recorded data\) · Confirmed \(simulated\)/,
    )

    await page.bringToFront()
    await expect(step(page, 'Approval')).toContainText(
      'Approved by Head Chef (demo) → Confirmed (simulated)',
    )

    // Decision log via GET /v0.1/audit?proposalId=
    await page.getByRole('link', { name: 'Open the decision log for this proposal' }).click()
    await expect(page).toHaveURL(/\/log\/PR-/)
    await expect(page.getByText('Synthetic mock data')).toHaveCount(0)
    const logEntries = page.getByRole('list', { name: 'Decision log' }).locator('> li')
    await expect(logEntries.filter({ hasText: 'Gateway decision' })).toContainText(
      'OVER_AUTO_LIMIT',
    )
    await expect(logEntries.filter({ hasText: 'Approved by a human' })).toContainText('Human')
    await expect(logEntries.last()).toContainText('Confirmed (simulated)')
    await page.getByRole('link', { name: 'Back to the purchasing flow' }).click()

    // 3. Scenario C -> BLOCK + INSTRUCTION_IGNORED
    await page.getByRole('button', { name: /^Scenario C:/ }).click()
    await expect(step(page, 'Approval')).toContainText('Blocked by mandate')
    await expect(step(page, 'Approval')).toContainText('Above hard limit')
    await expect(step(page, 'Approval')).toContainText('Instruction ignored')

    // 4. Finnish free text: "Tarvitsen tuhat litraa laktoositonta maitoa maanantaiksi" -> AUTO
    await page
      .getByLabel('What do you need, and by when?')
      .fill('Tarvitsen tuhat litraa laktoositonta maitoa maanantaiksi')
    await page.getByRole('button', { name: 'Run request' }).click()
    await expect(step(page, 'Approval')).toContainText('Agent decided → Confirmed (simulated)')
    await expect(step(page, 'Order')).toContainText('€1,010.00 excl. VAT')

    // 5. Clarification (O6): show the single question and let the buyer answer by editing the request
    await page
      .getByLabel('What do you need, and by when?')
      .fill('We need lactose-free milk by Monday.')
    await page.getByRole('button', { name: 'Run request' }).click()
    await expect(step(page, 'Request')).toContainText('CLARIFICATION NEEDED')
    await expect(
      page.getByRole('button', { name: 'Answer by editing the request above' }),
    ).toBeVisible()
    await page.getByRole('button', { name: 'Answer by editing the request above' }).click()
    await page
      .getByLabel('What do you need, and by when?')
      .fill('1,000 litres of lactose-free milk by Monday.')
    await page.getByRole('button', { name: 'Run request' }).click()
    await expect(step(page, 'Approval')).toContainText('Agent decided → Confirmed (simulated)')

    // 6. Verify no horizontal scroll at 390 px width
    await page.setViewportSize({ width: 390, height: 844 })
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - window.innerWidth,
    )
    expect(overflow).toBeLessThanOrEqual(0)

    // 7. Streamed ERROR event ("nothing was ordered") when upstream price/availability cannot be read
    await page.setViewportSize({ width: 1280, height: 900 })
    await page.route('**/v0.1/agent/run', async (route) => {
      const ndjson =
        [
          JSON.stringify({
            type: 'step',
            step: 1,
            name: 'REQUEST',
            title: 'Request',
            status: 'done',
            summary: '1 item · structured',
            request: {
              requestText: '10 litres of whipping cream by Monday.',
              requestedDate: '2026-11-16',
              items: [
                {
                  itemId: 'I1',
                  what: 'whipping cream',
                  quantity: '10',
                  unit: 'l',
                  requirements: [],
                  preferences: [],
                },
              ],
              assumptions: [],
            },
            reasons: [],
            excluded: [],
          }),
          JSON.stringify({
            type: 'error',
            step: 2,
            name: 'ERROR',
            title: 'Error',
            status: 'blocked',
            summary:
              'Live price/availability could not be read for this shortlist; nothing was ordered.',
            error: {
              code: 'UPSTREAM_UNAVAILABLE',
              message:
                'Live price/availability could not be read for this shortlist; nothing was ordered.',
            },
            reasons: [],
            excluded: [],
          }),
        ].join('\n') + '\n'
      await route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'application/x-ndjson' },
        body: ndjson,
      })
    })
    await page
      .getByLabel('What do you need, and by when?')
      .fill('10 litres of whipping cream by Monday.')
    await page.getByRole('button', { name: 'Run request' }).click()
    await expect(page.getByRole('alert').first()).toContainText('nothing was ordered')
    await expect(step(page, 'Lookup')).toContainText('Blocked')
    await page.unroute('**/v0.1/agent/run')

    await context.close()
  })
})
