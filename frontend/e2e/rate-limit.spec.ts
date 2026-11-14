import { expect, test } from '@playwright/test'
import { readFileSync } from 'node:fs'

const mandate = JSON.parse(readFileSync('src/mocks/mandate-m1.json', 'utf8'))

for (const mode of ['json', 'ndjson'] as const) {
  test(`shows Valio cooldown in ${mode} without switching to synthetic mode`, async ({ page }) => {
    const error = {
      code: 'VALIO_RATE_LIMITED',
      message: 'Valio API rate limit: retry in 30 s',
    }
    await page.route('**/health', (route) =>
      route.fulfill({ json: { status: 'ok', ready: true, mode: 'LIVE' } }),
    )
    await page.route('**/v0.1/mandates', (route) => route.fulfill({ json: [mandate] }))
    await page.route('**/v0.1/agent/run', (route) =>
      mode === 'json'
        ? route.fulfill({
            status: 503,
            headers: { 'Retry-After': '30' },
            json: { error: { ...error, details: { retryAfterSeconds: 30 } } },
          })
        : route.fulfill({
            contentType: 'application/x-ndjson',
            body:
              JSON.stringify({
                type: 'error',
                step: 2,
                name: 'ERROR',
                title: 'Error',
                status: 'blocked',
                summary: error.message,
                error,
              }) + '\n',
          }),
    )
    await page.goto('/')
    await page.getByLabel('What do you need, and by when?').fill('1,000 litres of milk by Monday')
    await page.getByRole('button', { name: 'Run request' }).click()
    await expect(
      page.getByRole('region', { name: 'Buyer request' }).getByRole('alert'),
    ).toContainText(error.message)
    await expect(page.getByText('Synthetic mock data')).toHaveCount(0)
    await expect(page.getByText('Simulated order — mock environment', { exact: true })).toHaveCount(
      0,
    )
    await expect(page.getByRole('button', { name: 'Run request' })).toBeEnabled()
  })
}
