import { defineConfig, devices } from '@playwright/test'
import path from 'node:path'
import { existsSync, mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'

const database = path.join(
  mkdtempSync(path.join(existsSync('/tmp/opencode') ? '/tmp/opencode' : tmpdir(), 'aimo-e2e-')),
  'browser.db',
)
export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  use: { baseURL: 'http://localhost:5175', trace: 'retain-on-failure' },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: [
    {
      command: `../.venv/bin/uvicorn app.main:app --app-dir ../backend --port 8010`,
      url: 'http://localhost:8010/health',
      reuseExistingServer: false,
      env: {
        DATABASE_URL: `sqlite:///${database}`,
        MOCK_MODE: 'true',
        OPENROUTER_API_KEY: '',
        FRONTEND_ORIGINS: 'http://localhost:5175',
      },
    },
    {
      command: 'npm run dev -- --port 5175',
      url: 'http://localhost:5175',
      reuseExistingServer: false,
      env: { VITE_API_BASE_URL: 'http://localhost:8010' },
    },
  ],
})
