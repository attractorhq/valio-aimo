import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import {
  clientKey,
  containerEnv,
  isGatewayPath,
  isMcpPath,
  isRateLimited,
  rateLimitedBody,
} from '../src/routes.ts'

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

test('every route that can trigger a live Valio lookup is rate limited', () => {
  const limited: [string, string][] = [
    ['POST', '/v0.1/agent/run'],
    ['POST', '/v0.1/offers'],
    ['POST', '/v0.1/proposals'],
    ['POST', '/v0.1/proposals/PR-1/anything'],
    ['PATCH', '/v0.1/admin/mandates/northbay-school-kitchen'],
    ['POST', '/v0.1/admin/mandates/northbay-school-kitchen/reset'],
    ['POST', '/v0.1/approver/proposals/PR-1/decision'],
    ['POST', '/mcp'],
    ['POST', '/mcp/messages'],
    ['post', '/v0.1/offers'],
  ]
  for (const [method, path] of limited) assert.equal(isRateLimited(method, path), true, `${method} ${path}`)
})

test('reads, static search, the approver view, health and the UI are never limited', () => {
  const free: [string, string][] = [
    ['GET', '/health'],
    ['GET', '/v0.1/audit'],
    ['GET', '/v0.1/proposals/PR-1'],
    ['GET', '/v0.1/mandates'],
    ['GET', '/v0.1/admin/mandates/northbay-school-kitchen/default'],
    ['GET', '/v0.1/approver/proposals'],
    ['GET', '/v0.1/approver/proposals/PR-1'],
    ['POST', '/v0.1/products/search'],
    ['GET', '/mcp'],
    ['OPTIONS', '/v0.1/offers'],
    ['HEAD', '/v0.1/offers'],
    ['GET', '/'],
    ['GET', '/spec'],
    ['GET', '/assets/index.js'],
    ['POST', '/v0.1/offersX'],
    ['POST', '/mcpx'],
  ]
  for (const [method, path] of free) assert.equal(isRateLimited(method, path), false, `${method} ${path}`)
})

test('429 bodies: the gateway error shape, or JSON-RPC for MCP', () => {
  assert.deepEqual(rateLimitedBody(false), {
    error: {
      code: 'RATE_LIMITED',
      message:
        'Too many requests from this client: live Valio lookups are rate limited. Retry in a minute.',
    },
  })
  const rpc = rateLimitedBody(true, 7) as { jsonrpc: string; id: unknown; error: { code: number } }
  assert.equal(rpc.jsonrpc, '2.0')
  assert.equal(rpc.id, 7)
  assert.equal(rpc.error.code, -32029)
  assert.equal(isMcpPath('/mcp'), true)
  assert.equal(isMcpPath('/v0.1/offers'), false)
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
    VALIO_MODE: '',
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
