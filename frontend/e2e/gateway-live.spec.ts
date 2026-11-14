import { expect, test, type Page } from '@playwright/test'
import { spawn, type ChildProcess } from 'node:child_process'
import path, { delimiter } from 'node:path'

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
    const pyBin = path.join(
      repoRoot,
      process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python',
    )
    backendProc = spawn(pyBin, ['-c', pyScript], {
      cwd: path.join(repoRoot, 'backend'),
      env: {
        ...process.env,
        PYTHONPATH: [path.join(repoRoot, 'backend'), path.join(repoRoot, 'backend/tests')].join(delimiter),
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
      process.execPath,
      ['node_modules/vite/bin/vite.js', '--host', '127.0.0.1', '--port', String(vitePort), '--strictPort'],
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
    // Connected to the real /v0.1 gateway: no data-source badges at all
    await expect(page.getByText('Synthetic mock data')).toHaveCount(0)

    const mandateRegion = page.getByRole('region', { name: 'Buyer mandate' })
    await expect(mandateRegion).toContainText('Nordic Harbour Hotels')
    await expect(mandateRegion).toContainText('Northbay School Kitchen')

    // 1. Official example typed in free text -> AUTO confirmed (€1,010.00)
    await page
      .getByLabel('What do you need, and by when?')
      .fill(
        'I need 1,000 litres of lactose-free milk by Monday. Find an option that meets our requirements based on price and availability.',
      )
    await page.getByRole('button', { name: 'Run request' }).click()
    await expect(step(page, 'Approval')).toContainText('Agent decided → Confirmed')
    await expect(step(page, 'Order')).toContainText('€1,010.00 excl. VAT')
    await expect(step(page, 'Order')).toContainText('464306')
    const altsLiveA = step(page, 'Order').getByRole('region', {
      name: 'Good / Better / Best alternatives',
    })
    await expect(altsLiveA).toBeVisible()
    // Which alternatives exist depends on the data source (live API vs a recorded capture), so assert the
    // recommendation and the tier structure, not specific alternative product ids.
    await expect(altsLiveA).toContainText('464306')
    await expect(altsLiveA).toContainText('Recommended')
    await expect(altsLiveA).toContainText('Best')

    // T49: the shortlist ids moved from visible text to a tooltip (data-shortlist / title).
    const shortlistLocator = step(page, 'Lookup').locator('small[data-shortlist]').first()
    await expect(shortlistLocator).toHaveAttribute('data-shortlist', /464306/)
    const shortlistBefore = (await shortlistLocator.getAttribute('data-shortlist')) ?? ''

    // 1b. T24: Changing "Any fat %" to "1.5 %" in A re-runs the flow and changes the shortlist
    await step(page, 'Request').getByRole('button', { name: 'Edit assumption: Any fat %' }).click()
    const fatInput = step(page, 'Request').getByRole('textbox', {
      name: 'Edit assumption Any fat %',
    })
    await fatInput.fill('1.5 %')
    await step(page, 'Request').getByRole('button', { name: 'Apply assumption Any fat %' }).click()
    await expect(step(page, 'Request')).toContainText('Preferences: 1.5 %')
    await expect(
      step(page, 'Request').getByRole('button', { name: 'Edit assumption: 1.5 %' }),
    ).toBeVisible()
    await expect(shortlistLocator).toHaveAttribute('data-shortlist', /464306/)
    await expect(shortlistLocator).not.toHaveAttribute('data-shortlist', shortlistBefore)
    await expect(shortlistLocator).not.toHaveAttribute('data-shortlist', /463335/)
    await expect(shortlistLocator).not.toHaveAttribute('data-shortlist', /464401/)
    await expect(shortlistLocator).not.toHaveAttribute('data-shortlist', /464073/)

    // 2. Scenario B -> ASK -> Approver panel -> Approve -> CONFIRMED_SIMULATED in both views
    await page.getByRole('button', { name: /^Scenario B:/ }).click()
    await expect(step(page, 'Order')).toContainText('€506.16 excl. VAT')
    const altsLiveB = step(page, 'Order').getByRole('region', {
      name: 'Good / Better / Best alternatives',
    })
    await expect(altsLiveB).toBeVisible()
    await expect(altsLiveB).toContainText('474235')
    await expect(altsLiveB).toContainText('468465')
    await expect(altsLiveB).toContainText('Best')
    await expect(altsLiveB).not.toContainText('470863')
    await expect(altsLiveB).not.toContainText('470699')
    await altsLiveB.getByRole('button', { name: 'Show trade-offs' }).click()
    await expect(altsLiveB).toContainText('SamMills')
    await expect(altsLiveB).toContainText('Oddlygood')
    await expect(altsLiveB).toContainText('in stock (verified)')
    await expect(step(page, 'Approval')).toContainText('Waiting for approver')
    await expect(step(page, 'Approval')).toContainText('Waiting for Head Chef')
    await expect(page.getByRole('button', { name: /approve/i })).toHaveCount(0)

    // Resubmitting the scenario through the composer must return the same immutable proposal.
    const firstLogHref = await page.getByRole('link', { name: 'Open decision log' }).getAttribute('href')
    const rerun = page.waitForResponse((response) =>
      response.url().endsWith('/v0.1/agent/run') && response.request().method() === 'POST',
    )
    await page.getByRole('button', { name: 'Run request' }).click()
    expect((await rerun).ok()).toBe(true)
    await expect(step(page, 'Approval')).toContainText('Waiting for approver')
    await expect(page.getByRole('link', { name: 'Open decision log' })).toHaveAttribute('href', firstLogHref!)

    const approverPage = await context.newPage()
    await approverPage.goto(`${baseURL}/approver`)
    await approverPage.getByLabel('Signed in as').selectOption('northbay-school-kitchen')
    await expect(
      approverPage.getByRole('heading', { name: 'Approver — Head Chef' }),
    ).toBeVisible()
    await expect(approverPage.getByRole('heading', { name: 'Proposals for you (1 waiting)' })).toBeVisible()
    const card = approverPage.getByRole('article')
    await expect(card.getByRole('heading')).toHaveText('Approving version 1')
    await expect(card).toContainText('€506.16 is above your €500.00 auto-approve limit.')
    const mobileHref = await card
      .getByRole('link', { name: 'Open mobile approval page' })
      .getAttribute('href')
    expect(mobileHref).toMatch(/^\/approve\/atk-.*\?proposalId=PR-/)

    // Verify phone approval page (/approve/:token) loads the exact proposal from the live gateway
    const phonePage = await context.newPage()
    await phonePage.goto(`${baseURL}${mobileHref}`)
    await expect(phonePage.getByRole('article')).toContainText('Approving version 1')
    await phonePage.close()

    await card.getByRole('button', { name: 'Approve €506.16' }).click()
    await expect(card).toContainText(/Approved · re-checked \(recorded data\) · Confirmed/)

    await expect(approverPage.getByRole('heading', { name: 'Proposals for you (0 waiting)' })).toBeVisible()
    await expect(card.getByRole('heading')).toHaveText('Version 1')
    await expect(card.getByRole('button', { name: /^(Approve|Reject)/ })).toHaveCount(0)

    await page.bringToFront()
    await expect(step(page, 'Approval')).toContainText('Approved by Head Chef → Confirmed')

    // Decision log via GET /v0.1/audit?proposalId=
    await page.getByRole('link', { name: 'Open decision log' }).click()
    await expect(page).toHaveURL(/\/log\/PR-/)
    await expect(page.getByText('Synthetic mock data')).toHaveCount(0)
    const logEntries = page.getByRole('list', { name: 'Decision log' }).locator('> li')
    await expect(logEntries.filter({ hasText: 'Gateway decision' })).toContainText(
      'OVER_AUTO_LIMIT',
    )
    await expect(logEntries.filter({ hasText: 'Approved by a human' })).toContainText('Human')
    await expect(logEntries.last()).toContainText('Confirmed')
    await page.getByRole('link', { name: 'Back to the purchasing flow' }).click()

    // 3. Scenario C -> BLOCK + INSTRUCTION_IGNORED
    await page.getByRole('button', { name: /^Scenario C:/ }).click()
    await expect(step(page, 'Approval')).toContainText('Blocked by mandate')
    await expect(step(page, 'Approval')).toContainText('Above hard limit')
    await expect(step(page, 'Approval')).toContainText('Instruction ignored')

    // 4. Finnish free text: "Tarvitsen tuhat litraa laktoositonta maitoa maanantaiksi" -> the same order.
    // T62 split-order guard: the same buyer already has €1,010.00 confirmed for Mon 16 Nov (step 1), so this
    // repeat order takes the day over the €1,500.00 auto-approve limit: ASK, and the text says why.
    await page
      .getByLabel('What do you need, and by when?')
      .fill('Tarvitsen tuhat litraa laktoositonta maitoa maanantaiksi')
    await page.getByRole('button', { name: 'Run request' }).click()
    await expect(step(page, 'Approval')).toContainText(
      '€1,010.00 excl. VAT plus the orders already committed for delivery on Mon 16 Nov exceeds your €1,500.00 auto-approve limit.',
    )
    await expect(page.getByTestId('result-why')).toContainText(
      'this order plus the orders already committed for delivery on Mon 16 Nov exceeds your €1,500.00 auto-approve limit',
    )
    await expect(step(page, 'Order')).toContainText('€1,010.00 excl. VAT')

    // 5. Clarification (O6): show the single question and let the buyer answer by editing the request
    await page
      .getByLabel('What do you need, and by when?')
      .fill('We need lactose-free milk by Monday.')
    await page.getByRole('button', { name: 'Run request' }).click()
    await expect(step(page, 'Request')).toContainText('Needs one detail')
    await expect(page.getByRole('button', { name: 'Edit request' })).toBeVisible()
    await page.getByRole('button', { name: 'Edit request' }).click()
    await page
      .getByLabel('What do you need, and by when?')
      .fill('1,000 litres of lactose-free milk by Monday.')
    await page.getByRole('button', { name: 'Run request' }).click()
    // The answered request is decided like any other order: ASK for the same reason as step 4 (T62).
    await expect(step(page, 'Approval')).toContainText(
      'already committed for delivery on Mon 16 Nov',
    )

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
