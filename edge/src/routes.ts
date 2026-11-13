// Which paths the edge Worker forwards to the gateway container. Everything else is the static UI.
// Keep this list in sync with `assets.run_worker_first` in wrangler.jsonc.

const EXACT = new Set(['/health', '/openapi.json', '/docs', '/mcp'])
const PREFIXES = ['/v0.1/', '/docs/', '/mcp/']

export function isGatewayPath(pathname: string): boolean {
  return EXACT.has(pathname) || PREFIXES.some((prefix) => pathname.startsWith(prefix))
}

/** The agent channel is rate limited; the approver channel and reads are not. */
export function isAgentPath(pathname: string): boolean {
  return pathname.startsWith('/v0.1/agent/')
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
