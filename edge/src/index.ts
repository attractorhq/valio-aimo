// Edge Worker "valio-aimo": one origin for the UI, the gateway API, MCP and approval links.
// Static assets (frontend/dist) are served by the assets binding with SPA fallback; the gateway paths
// listed in wrangler.jsonc run this Worker first and are forwarded to one FastAPI container.
import { Container, getContainer } from '@cloudflare/containers'
import { clientKey, containerEnv, isAgentPath, isGatewayPath, type ContainerEnvKey } from './routes.ts'

export type Env = Partial<Record<ContainerEnvKey, string>> & {
  ASSETS: Fetcher
  GATEWAY: DurableObjectNamespace<Gateway>
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

function tooManyRequests(): Response {
  return new Response(
    JSON.stringify({ detail: 'Too many agent requests from this client. Retry in a minute.' }),
    { status: 429, headers: { 'Content-Type': 'application/json', 'Retry-After': '60' } },
  )
}

export default {
  async fetch(request, env): Promise<Response> {
    const { pathname } = new URL(request.url)
    if (!isGatewayPath(pathname)) return env.ASSETS.fetch(request)
    if (isAgentPath(pathname) && env.AGENT_RATE_LIMIT) {
      const { success } = await env.AGENT_RATE_LIMIT.limit({ key: clientKey(request) })
      if (!success) return tooManyRequests()
    }
    return getContainer(env.GATEWAY).fetch(request)
  },
} satisfies ExportedHandler<Env>
