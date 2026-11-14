// Which paths the edge Worker forwards to the gateway container. Everything else is the static UI.
// Keep this list in sync with `assets.run_worker_first` in wrangler.jsonc.

const EXACT = new Set(['/health', '/openapi.json', '/docs', '/mcp'])
const PREFIXES = ['/v0.1/', '/docs/', '/mcp/']

export function isGatewayPath(pathname: string): boolean {
  return EXACT.has(pathname) || PREFIXES.some((prefix) => pathname.startsWith(prefix))
}

const WRITE_METHODS = new Set(['POST', 'PUT', 'PATCH', 'DELETE'])

/**
 * Routes that can trigger live Valio price + availability lookups on the event API key (API etiquette,
 * CLAUDE.md rule 6) share one per-IP limit: the agent run, getOffer, createProposal (re-check), buyer-admin
 * changes (re-decide live), the approver's decision (re-check) and MCP tool calls. GET reads, the static
 * search and the UI are never limited.
 */
export function isRateLimited(method: string, pathname: string): boolean {
  if (!WRITE_METHODS.has(method.toUpperCase())) return false
  return (
    pathname.startsWith('/v0.1/agent/') ||
    pathname === '/v0.1/offers' ||
    pathname.startsWith('/v0.1/offers/') ||
    pathname === '/v0.1/proposals' ||
    pathname.startsWith('/v0.1/proposals/') ||
    pathname.startsWith('/v0.1/admin/') ||
    /^\/v0\.1\/approver\/proposals\/[^/]+\/decision$/.test(pathname) ||
    pathname === '/mcp' ||
    pathname.startsWith('/mcp/')
  )
}

export function isMcpPath(pathname: string): boolean {
  return pathname === '/mcp' || pathname.startsWith('/mcp/')
}

export const RATE_LIMIT_MESSAGE =
  'Too many requests from this client: live Valio lookups are rate limited. Retry in a minute.'

/** 429 in the gateway's error shape, or a JSON-RPC error for MCP clients (echoing the request id). */
export function rateLimitedBody(mcp: boolean, rpcId: string | number | null = null): unknown {
  if (mcp) {
    return { jsonrpc: '2.0', id: rpcId, error: { code: -32029, message: RATE_LIMIT_MESSAGE } }
  }
  return { error: { code: 'RATE_LIMITED', message: RATE_LIMIT_MESSAGE } }
}

/** One rate-limit bucket per client IP (Cloudflare sets CF-Connecting-IP at the edge). */
export function clientKey(request: Request): string {
  return request.headers.get('CF-Connecting-IP') ?? 'unknown-client'
}

// Container environment: secrets and vars set on the Worker, passed through by name only.
export const CONTAINER_ENV_KEYS = [
  'VALIO_API_KEY',
  'OPENROUTER_API_KEY',
  'APPROVAL_SIGNING_KEY',
  'LLM_MODEL',
  'VALIO_MODE',
  'VALIO_BASE_URL',
  'VALIO_CUSTOMER_ID',
  'VALIO_CUSTOMER_IDS',
  'FRONTEND_ORIGINS',
] as const

export type ContainerEnvKey = (typeof CONTAINER_ENV_KEYS)[number]

export function containerEnv(env: Partial<Record<ContainerEnvKey, unknown>>): Record<string, string> {
  const out: Record<string, string> = {}
  for (const key of CONTAINER_ENV_KEYS) {
    const value = env[key]
    if (typeof value === 'string' && value !== '') out[key] = value
  }
  return out
}
