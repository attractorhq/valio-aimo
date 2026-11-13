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
  use: { baseURL: 'http://localhost:5173', trace: 'retain-on-failure' },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: [
    {
      command: `../.venv/bin/uvicorn app.main:app --app-dir ../backend --port 8000`,
      url: 'http://localhost:8000/health',
      reuseExistingServer: false,
      env: {
        DATABASE_URL: `sqlite:///${database}`,
        MOCK_MODE: 'true',
        DEEPSEEK_API_KEY: '',
        FRONTEND_ORIGINS: 'http://localhost:5173',
      },
    },
    {
      command: 'npm run dev -- --port 5173',
      url: 'http://localhost:5173',
      reuseExistingServer: false,
    },
  ],
})
