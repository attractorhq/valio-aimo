// Edge Worker "valio-aimo": one origin for the UI, the gateway API, MCP and approval links.
// Static assets (frontend/dist) are served by the assets binding with SPA fallback; the gateway paths
// listed in wrangler.jsonc run this Worker first and are forwarded to one FastAPI container.
import { Container, getContainer } from '@cloudflare/containers'
import {
  clientKey,
  containerEnv,
  isGatewayPath,
  isMcpPath,
  isRateLimited,
  rateLimitedBody,
  type ContainerEnvKey,
} from './routes.ts'

export type Env = Partial<Record<ContainerEnvKey, string>> & {
  ASSETS: Fetcher
  GATEWAY: DurableObjectNamespace<Gateway>
  /** Per-IP limit shared by every route that can trigger a live Valio lookup (see isRateLimited). */
  AGENT_RATE_LIMIT?: RateLimit
}

/** The FastAPI gateway. One instance; its SQLite state resets on restart/deploy (accepted, WORKFLOW.md). */
export class Gateway extends Container<Env> {
  defaultPort = 8000
  // Keep the demo warm between sessions; a cold start re-loads the catalogue.
  sleepAfter = '6h'
  // Worker secrets and vars → container env, by name only. Values are never logged.
  envVars = containerEnv(this.env)
}

async function rpcId(request: Request): Promise<string | number | null> {
  try {
    const body = (await request.clone().json()) as { id?: unknown }
    return typeof body?.id === 'string' || typeof body?.id === 'number' ? body.id : null
  } catch {
    return null // batch, notification or not JSON: the JSON-RPC id is null
  }
}

async function tooManyRequests(request: Request, pathname: string): Promise<Response> {
  const mcp = isMcpPath(pathname)
  const body = rateLimitedBody(mcp, mcp ? await rpcId(request) : null)
  return new Response(JSON.stringify(body), {
    status: 429,
    headers: { 'Content-Type': 'application/json', 'Retry-After': '60' },
  })
}

export default {
  async fetch(request, env): Promise<Response> {
    const { pathname } = new URL(request.url)
    if (!isGatewayPath(pathname)) return env.ASSETS.fetch(request)
    if (isRateLimited(request.method, pathname) && env.AGENT_RATE_LIMIT) {
      const { success } = await env.AGENT_RATE_LIMIT.limit({ key: clientKey(request) })
      if (!success) return tooManyRequests(request, pathname)
    }
    return getContainer(env.GATEWAY).fetch(request)
  },
} satisfies ExportedHandler<Env>
