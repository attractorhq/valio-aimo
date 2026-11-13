import { defineConfig, devices } from '@playwright/test'
import { existsSync, mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'

const directory = mkdtempSync(
  path.join(existsSync('/tmp/opencode') ? '/tmp/opencode' : tmpdir(), 'aimo-live-'),
)
export default defineConfig({
  testDir: './e2e-live',
  workers: 1,
  timeout: 90_000,
  use: { baseURL: 'http://localhost:5174', trace: 'retain-on-failure' },
  projects: [{ name: 'live-challenge-api', use: { ...devices['Desktop Chrome'] } }],
  webServer: [
    {
      command:
        '../.venv/bin/python ../scripts/import_products.py && ../.venv/bin/uvicorn app.main:app --app-dir ../backend --port 8011',
      url: 'http://localhost:8011/health',
      timeout: 180_000,
      reuseExistingServer: false,
      env: {
        DATABASE_URL: `sqlite:///${path.join(directory, 'browser.db')}`,
        MOCK_MODE: 'false',
        DEEPSEEK_API_KEY: '',
        FRONTEND_ORIGINS: 'http://localhost:5174',
      },
    },
    {
      command: 'npm run dev -- --port 5174',
      url: 'http://localhost:5174',
      reuseExistingServer: false,
      env: { VITE_API_BASE_URL: 'http://localhost:8011' },
    },
  ],
})
