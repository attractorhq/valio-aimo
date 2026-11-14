import { expect, test, type Page } from '@playwright/test'
import { spawn, type ChildProcess } from 'node:child_process'
import path from 'node:path'

const step = (page: Page, name: string) => page.getByRole('listitem', { name: new RegExp(name) })

type DefaultMandateSpec = {
  customerId: string
  autoApproveLimitEur: string
  hardLimitEur: string
  allergenMode: 'STANDARD' | 'STRICT'
  autoAcceptValioSubstitutes: boolean
  autoAcceptSupplierStock: boolean
  minReliability: string
}

const DEFAULT_MANDATES: readonly DefaultMandateSpec[] = [
  {
    customerId: 'nordic-harbour-hotels',
    autoApproveLimitEur: '1500.00',
    hardLimitEur: '5000.00',
    allergenMode: 'STANDARD',
    autoAcceptValioSubstitutes: true,
    autoAcceptSupplierStock: false,
    minReliability: '0.80',
  },
  {
    customerId: 'northbay-school-kitchen',
    autoApproveLimitEur: '500.00',
    hardLimitEur: '3000.00',
    allergenMode: 'STRICT',
    autoAcceptValioSubstitutes: false,
    autoAcceptSupplierStock: false,
    minReliability: '0.80',
  },
]

/**
 * Reset the gateway and browser demo state before a presentation or test run:
 * 1. Restore buyer mandates M1 and M2 to their shipped defaults via `/v0.1/admin/mandates/{id}` if changed.
 * 2. Clear any pending proposals on `/v0.1/approver/proposals?pendingOnly=true` so the Approver queue starts at 0.
 * 3. Clear browser localStorage keys used by the demo UI.
 */
async function resetDemoState(
  apiBase: string,
  page?: Page,
): Promise<{ mode: 'LIVE' | 'RECORDED' }> {
  const cleanApi = apiBase.replace(/\/+$/, '')

  const healthRes = await fetch(`${cleanApi}/health`, {
    headers: { Accept: 'application/json' },
  })
  expect(healthRes.ok).toBeTruthy()
  const health = (await healthRes.json()) as {
    status: string
    ready: boolean
    mode?: 'LIVE' | 'RECORDED'
  }
  expect(health.ready).toBe(true)
  const mode = health.mode ?? 'RECORDED'

  // 1. Restore M1 and M2 mandates if any buyer-admin toggle modified them
  for (const spec of DEFAULT_MANDATES) {
    const mRes = await fetch(`${cleanApi}/v0.1/mandates/${encodeURIComponent(spec.customerId)}`, {
      headers: { Accept: 'application/json' },
    })
    if (!mRes.ok) continue
    const current = (await mRes.json()) as Record<string, unknown>
    const patch: Record<string, unknown> = {}
    if (String(current.autoApproveLimitEur) !== spec.autoApproveLimitEur) {
      patch.autoApproveLimitEur = spec.autoApproveLimitEur
    }
    if (String(current.hardLimitEur) !== spec.hardLimitEur) {
      patch.hardLimitEur = spec.hardLimitEur
    }
    if (current.allergenMode !== spec.allergenMode) {
      patch.allergenMode = spec.allergenMode
    }
    if (current.autoAcceptValioSubstitutes !== spec.autoAcceptValioSubstitutes) {
      patch.autoAcceptValioSubstitutes = spec.autoAcceptValioSubstitutes
    }
    if (current.autoAcceptSupplierStock !== spec.autoAcceptSupplierStock) {
      patch.autoAcceptSupplierStock = spec.autoAcceptSupplierStock
    }
    if (Number(current.minReliability) !== Number(spec.minReliability)) {
      patch.minReliability = spec.minReliability
    }
    if (Object.keys(patch).length > 0) {
      const pRes = await fetch(
        `${cleanApi}/v0.1/admin/mandates/${encodeURIComponent(spec.customerId)}`,
        {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
          body: JSON.stringify(patch),
        },
      )
      expect(pRes.ok).toBeTruthy()
    }
  }

  // 2. Clear any stale PENDING_APPROVAL proposals from the Approver queue
  const pendingRes = await fetch(`${cleanApi}/v0.1/approver/proposals?pendingOnly=true`, {
    headers: { Accept: 'application/json' },
  })
  if (pendingRes.ok) {
    const pendingList = (await pendingRes.json()) as Array<{
      proposalId: string
      offerVersion: number
    }>
    for (const p of pendingList) {
      const viewRes = await fetch(
        `${cleanApi}/v0.1/approver/proposals/${encodeURIComponent(p.proposalId)}`,
        { headers: { Accept: 'application/json' } },
      )
      if (!viewRes.ok) continue
      const viewData = (await viewRes.json()) as {
        approvalToken: string
        proposal: { offerVersion: number }
      }
      await fetch(
        `${cleanApi}/v0.1/approver/proposals/${encodeURIComponent(p.proposalId)}/decision`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
          body: JSON.stringify({
            offerVersion: viewData.proposal.offerVersion,
            approvalToken: viewData.approvalToken,
            action: 'REJECT',
            comment: 'Demo state reset before presentation',
          }),
        },
      )
    }
  }

  // 3. Clear browser storage if a page is active on the origin
  if (page && page.url().startsWith('http')) {
    await page.evaluate(() => {
      try {
        localStorage.removeItem('verified-or-ask.mock-gateway.v1')
        localStorage.removeItem('verified-or-ask.token-proposal-map.v1')
      } catch {
        // ignore storage errors
      }
    })
  }

  return { mode }
}

test.describe('P12 live demo kit: A (AUTO) → B (ASK; approver approves v1) → C (BLOCK) (T18)', () => {
  test.setTimeout(120_000)

  let backendProc: ChildProcess | null = null
  let viteProc: ChildProcess | null = null
  const backendPort = 8019
  const vitePort = 5189

  const externalUrl = process.env.DEMO_URL?.trim()
  const baseURL = externalUrl ? externalUrl.replace(/\/+$/, '') : `http://127.0.0.1:${vitePort}`
  const apiBaseURL = (
    process.env.DEMO_API_URL?.trim() ||
    externalUrl ||
    `http://127.0.0.1:${backendPort}`
  ).replace(/\/+$/, '')

  test.beforeAll(async () => {
    if (externalUrl) {
      // Running against a deployed URL or an externally started gateway (via scripts/demo)
      return
    }

    const repoRoot = path.resolve(process.cwd(), '..')
    // Opt-in live setup pattern from playwright.live.config.ts:
    // Uses LiveValioProvider when catalogue + VALIO_API_KEY are present and VALIO_MODE != 'recorded';
    // otherwise uses RecordedValioProvider or ReferenceFixtureProvider so CI stays deterministic.
    const pyScript = `
import os, uvicorn
from test_agent_run import _build_synthetic_catalog, SyntheticAgentProvider, NOW
from app.api.state import DEFAULT_FIXTURES_DIR, GatewayState, build_state, get_state, latest_capture, try_state
from app.catalog.build import DEFAULT_ARTIFACT_PATH
from app.gateway.proposals import ProposalGateway
from app.main import app
from app.policy import DEMO_MANDATES, MandateBook

state = None
if DEFAULT_ARTIFACT_PATH.exists():
    try:
        state = build_state()
    except Exception:
        state = None

if state is None:
    products = _build_synthetic_catalog()
    provider = SyntheticAgentProvider(now=NOW)
    gateway = ProposalGateway(
        products=products,
        mandates=MandateBook(DEMO_MANDATES),
        provider=provider,
    )
    state = GatewayState(
        gateway=gateway,
        catalog=list(products.values()),
        mode="RECORDED",
        clock=lambda: NOW,
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
        FRONTEND_ORIGINS: `http://127.0.0.1:${vitePort},http://localhost:${vitePort}`,
      },
      stdio: 'pipe',
    })

    for (let i = 0; i < 60; i++) {
      try {
        const res = await fetch(`http://127.0.0.1:${backendPort}/health`)
        if (res.ok) {
          const data = (await res.json()) as { ready?: boolean }
          if (data.ready) break
        }
      } catch {
        // wait for backend startup
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
        // wait for vite startup
      }
      await new Promise((r) => setTimeout(r, 200))
    }
  })

  test.afterAll(async () => {
    viteProc?.kill('SIGTERM')
    backendProc?.kill('SIGTERM')
  })

  test('runs demo-state reset, pre-flight / mode check, and A (AUTO) → B (ASK + Approver v1) → C (BLOCK)', async ({
    browser,
  }, info) => {
    const context = await browser.newContext({ viewport: { width: 1280, height: 900 } })
    const page = await context.newPage()

    // 0. Demo-state reset & honest mode / pre-flight verification
    const { mode } = await resetDemoState(apiBaseURL)
    await page.route('**/v0.1/proposals/PR-*', async (route) => {
      const response = await route.fetch()
      if (response.ok()) {
        const body = (await response.json()) as { state?: string }
        if (body.state === 'APPROVED' || body.state === 'RECHECKED') {
          await new Promise((r) => setTimeout(r, 350))
          const retry = await route.fetch()
          await route.fulfill({ response: retry })
          return
        }
        await route.fulfill({
          status: response.status(),
          headers: response.headers(),
          body: JSON.stringify(body),
        })
        return
      }
      await route.fulfill({ response })
    })
    await page.goto(baseURL)
    await resetDemoState(apiBaseURL, page)

    // Must drive the real /v0.1 gateway, never synthetic mocks
    await expect(page.getByText('Synthetic mock data')).toHaveCount(0)
    // No data-source badges in the UI (team decision 14 Nov).
    await expect(page.getByText(/Recorded · captured/)).toHaveCount(0)
    // The live pre-flight is a presenter command (scripts/preflight), not a buyer-view control.
    await expect(page.getByRole('button', { name: 'Pre-flight', exact: true })).toHaveCount(0)

    // 1. Scenario A (AUTO): 464306 × 1,000 = €1,010.00 excl. VAT
    await page.getByRole('button', { name: /^Scenario A:/ }).click()
    await expect(step(page, 'Request')).toContainText('Lactose-free')
    await expect(step(page, 'Request')).toContainText('Mon 16 Nov')
    await expect(step(page, 'Order')).toContainText(
      'Proposal at fixed price · offer v1 · €1,010.00 excl. VAT',
    )
    await expect(step(page, 'Order')).toContainText('464306')
    await expect(step(page, 'Approval')).toContainText('Agent decided → Confirmed (simulated)')
    await expect(step(page, 'Approval')).toContainText('Simulated order — mock environment')
    // T60: Valio's availability sentinel (>= 999999) is "No stock limit" at DECLARED, never "Plenty in stock".
    const cardA = page.getByRole('article', { name: /Trust card for/ }).first()
    await cardA.getByRole('button', { name: /Show facts/ }).click()
    const stockRow = cardA.locator('dl > div').filter({ hasText: /^Stock/ })
    await expect(stockRow).toContainText('No stock limit: Valio allows any quantity')
    await expect(stockRow).toContainText(/declared/i)
    await expect(cardA).not.toContainText('Plenty in stock')

    // 2. Scenario B (ASK; the approver approves v1): €506.16 > €500.00 for M2 (STRICT)
    await page.getByRole('button', { name: /^Scenario B:/ }).click()
    await expect(step(page, 'Order')).toContainText(
      'Proposal at fixed price · offer v1 · €506.16 excl. VAT',
    )
    await expect(step(page, 'Order')).toContainText('474235')
    await expect(step(page, 'Order')).toContainText('468465')
    await expect(page.getByRole('note', { name: 'Valio-substitute check' })).toBeVisible()
    await expect(step(page, 'Lookup')).toContainText('470863')
    await expect(step(page, 'Lookup')).toContainText('Substitute rejected')

    const approvalStepB = step(page, 'Approval')
    await expect(approvalStepB).toContainText('Waiting for approver')
    await expect(approvalStepB).toContainText('Waiting for Head Chef (demo)')
    await expect(
      approvalStepB.getByRole('list', { name: 'Reasons' }).getByRole('listitem'),
    ).toHaveCount(1)
    await expect(approvalStepB).toContainText(
      '€506.16 excl. VAT is above your €500.00 auto-approve limit.',
    )
    // Agent view never has an approve button or approval link/QR (P3, AC15)
    await expect(page.getByRole('button', { name: /approve/i })).toHaveCount(0)
    await expect(page.getByRole('img', { name: /Scan on a phone/i })).toHaveCount(0)

    // Separate Approver panel signs off version 1
    const approverPage = await context.newPage()
    await approverPage.goto(`${baseURL}/approver`)
    await expect(
      approverPage.getByRole('heading', { name: 'Approver — Head Chef (demo)' }),
    ).toBeVisible()
    await expect(
      approverPage.getByRole('heading', { name: /Proposals for you \(1 waiting\)/ }),
    ).toBeVisible()

    const card = approverPage.getByRole('article')
    await expect(card.getByRole('heading')).toHaveText(
      'You are approving version 1 of this proposal',
    )
    await expect(card).toContainText(
      '€506.16 is above your €500.00 auto-approve limit. Everything else is verified or declared.',
    )
    await expect(card).toContainText('Demo approval link — single use')

    await card.getByRole('button', { name: 'Approve €506.16' }).click()
    await expect(card).toContainText(
      /Approved · re-checked (live|\(recorded data\)) · Confirmed \(simulated\)/,
    )
    await approverPage.close()

    await page.bringToFront()
    await page.evaluate(() => window.dispatchEvent(new Event('focus')))
    await expect(step(page, 'Approval')).toContainText(
      'Approved by Head Chef (demo) → Confirmed (simulated)',
    )

    // 3. Scenario C (BLOCK): 464306 × 20,000 = €20,200.00 > €5,000.00 hard limit + INSTRUCTION_IGNORED
    await page.getByRole('button', { name: /^Scenario C:/ }).click()
    await expect(step(page, 'Order')).toContainText('€20,200.00 excl. VAT')
    const approvalStepC = step(page, 'Approval')
    await expect(approvalStepC).toContainText('Blocked by mandate')
    await expect(approvalStepC).toContainText('Above hard limit')
    await expect(approvalStepC).toContainText(
      '€20,200.00 excl. VAT is above your €5,000.00 hard limit.',
    )
    await expect(approvalStepC).toContainText('Instruction ignored')
    await expect(page.getByRole('button', { name: /approve/i })).toHaveCount(0)

    await page.screenshot({
      path: `test-results/demo-flow-${info.project.name}.png`,
      fullPage: true,
    })

    // 4. Verify demo-state reset restores modified mandates and clears pending proposals
    const patchRes = await fetch(`${apiBaseURL}/v0.1/admin/mandates/nordic-harbour-hotels`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({ autoApproveLimitEur: '1600.00' }),
    })
    expect(patchRes.ok).toBeTruthy()
    await page.reload()
    const mandateRegion = page.getByRole('region', { name: 'Buyer mandate' })
    await expect(mandateRegion).toContainText('€1,600.00 excl. VAT')

    await resetDemoState(apiBaseURL, page)
    await page.reload()
    await expect(mandateRegion).toContainText('€1,500.00 excl. VAT')

    await context.close()
  })
})
