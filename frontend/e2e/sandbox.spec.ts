import { expect, test } from '@playwright/test'
import { execFile, spawn, type ChildProcess } from 'node:child_process'
import { createServer } from 'node:net'
import path from 'node:path'
import { promisify } from 'node:util'

const exec = promisify(execFile)
const apiPort = 8042
const vitePort = 5192
const apiURL = `http://127.0.0.1:${apiPort}`
const baseURL = `http://127.0.0.1:${vitePort}`
const hotel = 'nordic-harbour-hotels'
const school = 'northbay-school-kitchen'

async function requireFreePort(port: number) {
  const server = createServer()
  await new Promise<void>((resolve, reject) => {
    server.once('error', reject)
    server.listen(port, '127.0.0.1', () => server.close(() => resolve()))
  })
}

function stopOwnedGroup(process: ChildProcess | undefined) {
  if (!process?.pid) return
  try {
    globalThis.process.kill(-process.pid, 'SIGTERM')
  } catch {
    // The owned process group has already exited; never kill another test's server.
  }
}

test.describe('private demo sessions', () => {
  test.setTimeout(90_000)
  let backend: ChildProcess | undefined
  let vite: ChildProcess | undefined
  const root = path.resolve(process.cwd(), '..')
  const python = path.join(root, '.venv/bin/python')

  test.beforeAll(async () => {
    await requireFreePort(apiPort)
    await requireFreePort(vitePort)
    const script = `
import uvicorn
from test_agent_run import _build_synthetic_catalog, SyntheticAgentProvider, NOW
from app.api import state as wiring
from app.api.state import GatewayState
from app.gateway.proposals import ProposalGateway
from app.main import app
products = _build_synthetic_catalog()
wiring._state = GatewayState(ProposalGateway(products=products, provider=SyntheticAgentProvider()),
    list(products.values()), "RECORDED", clock=lambda: NOW)
wiring._registry = None
uvicorn.run(app, host="127.0.0.1", port=${apiPort}, log_level="warning")
`
    backend = spawn(python, ['-c', script], {
      cwd: path.join(root, 'backend'),
      env: {
        ...process.env,
        PYTHONPATH: `${path.join(root, 'backend')}:${path.join(root, 'backend/tests')}`,
        OPENROUTER_API_KEY: '',
      },
      detached: true,
      stdio: 'pipe',
    })
    vite = spawn('npx', ['vite', '--host', '127.0.0.1', '--port', `${vitePort}`, '--strictPort'], {
      cwd: process.cwd(),
      env: { ...process.env, VITE_API_BASE_URL: apiURL },
      detached: true,
      stdio: 'pipe',
    })
    for (const url of [`${apiURL}/health`, baseURL]) {
      await expect
        .poll(
          async () => {
            try {
              return (await fetch(url)).ok
            } catch {
              return false
            }
          },
          { timeout: 30_000 },
        )
        .toBe(true)
    }
  })

  test.afterAll(() => {
    stopOwnedGroup(vite)
    stopOwnedGroup(backend)
  })

  test('admin edits, approvals and resets stay private; phone capability and standalone client still work', async ({
    browser,
  }) => {
    const first = await browser.newContext()
    const second = await browser.newContext()
    const phone = await browser.newContext()
    try {
      const admin = await first.newPage()
      await admin.goto(`${baseURL}/admin`)
      await admin.getByLabel('Auto-approve limit (€, excl. VAT)').fill('100')
      await admin.getByRole('button', { name: 'Apply policy change' }).click()
      await expect
        .poll(
          async () =>
            (await (await first.request.get(`${baseURL}/v0.1/mandates/${hotel}`)).json())
              .autoApproveLimitEur,
        )
        .toBe('100.00')

      const buyer = await second.newPage()
      await buyer.goto(baseURL)
      await buyer.getByRole('button', { name: /^Scenario A:/ }).click()
      await expect(buyer.getByRole('listitem', { name: /Approval/ })).toContainText(
        'Confirmed (simulated)',
      )
      await expect(buyer.getByRole('listitem', { name: /Order/ })).toContainText('€1,010.00')
      const firstCookie = (await first.cookies()).find((cookie) => cookie.name === 'voa_sandbox')!
      const secondCookie = (await second.cookies()).find((cookie) => cookie.name === 'voa_sandbox')!
      expect(firstCookie.value).not.toBe(secondCookie.value)
      expect(firstCookie.httpOnly).toBe(true)

      await admin.goto(baseURL)
      await admin.getByRole('button', { name: /^Scenario A:/ }).click()
      await expect(admin.getByRole('listitem', { name: /Approval/ })).toContainText(
        'Waiting for approver',
      )
      expect(
        await (
          await second.request.get(`${baseURL}/v0.1/approver/proposals?pendingOnly=true`)
        ).json(),
      ).toEqual([])
      const panel = await first.newPage()
      await panel.goto(`${baseURL}/approver`)
      await panel.getByLabel('Signed in as').selectOption(hotel)
      await expect(panel.getByRole('link', { name: 'Open mobile approval page' })).toBeVisible()
      const link = await panel
        .getByRole('link', { name: 'Open mobile approval page' })
        .getAttribute('href')
      expect(link).toContain('.sbx1.')
      const approval = await phone.newPage()
      await approval.goto(`${baseURL}${link}`)
      await expect(approval.getByRole('article')).toContainText('€1,010.00')
      await approval.getByRole('button', { name: 'Approve €1,010.00' }).click()
      await expect(approval.getByRole('article')).toContainText('Confirmed (simulated)')

      await expect(buyer.getByText('Your private demo session')).toBeVisible()
      await buyer.getByRole('button', { name: 'Reset', exact: true }).click()
      await expect(buyer.getByRole('listitem', { name: /Order/ })).toContainText('Not started')
      await buyer.getByRole('button', { name: /^Scenario A:/ }).click()
      await expect(buyer.getByRole('listitem', { name: /Approval/ })).toContainText(
        'Confirmed (simulated)',
      )
      expect(
        (await (await first.request.get(`${baseURL}/v0.1/approver/proposals`)).json())[0].state,
      ).toBe('CONFIRMED_SIMULATED')

      const reset = await exec(python, [path.join(root, 'scripts/demo'), '--reset', apiURL], {
        env: { ...process.env, DEMO_SANDBOX: firstCookie.value },
      })
      expect(reset.stdout).toContain('demo-reset: ok')
      expect(
        (await (await first.request.get(`${baseURL}/v0.1/mandates/${hotel}`)).json()).version,
      ).toBe(1)
      expect(
        (await (await first.request.get(`${baseURL}/v0.1/audit/verify`)).json()).chainLength,
      ).toBe(0)

      const standalone = JSON.parse(
        (await exec(python, [path.join(root, 'scripts/second-client'), apiURL, '--json'])).stdout,
      )
      const browserB = await (
        await second.request.post(`${baseURL}/v0.1/agent/run`, {
          data: {
            customerId: school,
            requestText:
              'We need 90 kg of gluten-free macaroni and 80 litres of gluten-free oat drink by Monday.',
          },
        })
      ).json()
      expect(standalone.decision).toBe('ASK')
      expect(standalone.totals.exclVat).toBe('506.16')
      expect(standalone.offerHash).toBe(browserB.proposal.offerHash)
    } finally {
      await phone.close()
      await second.close()
      await first.close()
    }
  })
})
