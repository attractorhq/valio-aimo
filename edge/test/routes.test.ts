import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { clientKey, containerEnv, isAgentPath, isGatewayPath } from '../src/routes.ts'

test('gateway paths go to the container, everything else is the UI', () => {
  for (const path of ['/health', '/openapi.json', '/docs', '/docs/oauth2-redirect', '/mcp', '/mcp/sse',
    '/v0.1/offers', '/v0.1/agent/run', '/v0.1/approver/proposals/P1']) {
    assert.equal(isGatewayPath(path), true, path)
  }
  for (const path of ['/', '/approver', '/log/MOCK-P-B', '/assets/index.js', '/v0.10/x', '/healthz',
    '/documents', '/api/v1/products']) {
    assert.equal(isGatewayPath(path), false, path)
  }
})

test('only the agent channel is rate limited', () => {
  assert.equal(isAgentPath('/v0.1/agent/run'), true)
  assert.equal(isAgentPath('/v0.1/approver/proposals/P1/decision'), false)
  assert.equal(isAgentPath('/v0.1/offers'), false)
})

test('rate limit key is the client IP', () => {
  const request = new Request('https://example.test/v0.1/agent/run', {
    headers: { 'CF-Connecting-IP': '203.0.113.7' },
  })
  assert.equal(clientKey(request), '203.0.113.7')
  assert.equal(clientKey(new Request('https://example.test/')), 'unknown-client')
})

test('only known, non-empty settings reach the container', () => {
  const env = containerEnv({
    VALIO_API_KEY: 'secret-value',
    LLM_MODEL: 'model',
    MOCK_MODE: '',
    // @ts-expect-error bindings and unknown keys are never forwarded
    ASSETS: {},
  })
  assert.deepEqual(env, { VALIO_API_KEY: 'secret-value', LLM_MODEL: 'model' })
})

test('wrangler run_worker_first matches the routing table', () => {
  const raw = readFileSync(new URL('../wrangler.jsonc', import.meta.url), 'utf8')
  const json = JSON.parse(raw.replace(/^\s*\/\/.*$/gm, ''))
  const patterns: string[] = json.assets.run_worker_first
  const sample = (pattern: string) => pattern.replace('/*', '/x')
  for (const pattern of patterns) assert.equal(isGatewayPath(sample(pattern)), true, pattern)
  assert.equal(json.containers[0].max_instances, 1)
  assert.equal(json.containers[0].instance_type, 'standard-1')
  assert.equal(json.assets.not_found_handling, 'single-page-application')
})
